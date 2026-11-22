"""Track new Form 4 filings and later contract evidence in separate forward cohorts."""

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from collect_usaspending import merge_records, output_lock, write_json_lines
from forward_contract_test import (
    FRESH_DAYS,
    MAX_BATCH_AGE,
    check_context,
    company_rows,
    eligible_context,
    entry_window,
    load_scores,
    measure,
    time_utc,
)
from score_contract_events import ROOT, digest, read_lines

CONTRACTS = ROOT / "forward_contract_test"


def insider_candidates(source_rows, now):
    for row in source_rows:
        summary = row["source_record"]
        if row["source"] != "sec_form4" or summary["form"] != "4":
            continue
        accepted = time_utc(summary["accepted_at_utc"])
        observed = time_utc(row["first_observed_at_utc"])
        if not now - timedelta(days=FRESH_DAYS) <= accepted <= observed <= now:
            continue
        context = eligible_context(
            [item for item in source_rows if item["source"] != "sec_form4"],
            row["ticker"], "", now,
        )
        event = f"{row['ticker']}:FORM4:{summary['accession']}"
        yield {
            "event_id": event, "ticker": row["ticker"], "version_sha256": digest(event),
            "summary": summary, "source_version": row["version_sha256"],
            "source_url": row["source_url"], "first_observed_at_utc": row["first_observed_at_utc"],
            "frozen_at_utc": now.isoformat(), "company_evidence": context,
        }


def later_candidates(source_rows, contracts, now):
    original = {item["event_id"]: item for item in read_lines(contracts / "candidates.txt")}
    for enrollment in read_lines(contracts / "enrollments.txt"):
        started = time_utc(enrollment["registered_at_utc"])
        if not started <= now <= started + timedelta(days=45):
            continue
        parent = enrollment["event_id"]
        packet = original[parent]
        newer = eligible_context(source_rows, enrollment["ticker"], packet["summary"]["award_id"],
                                 now, after=started)
        if not newer:
            continue
        key = digest([parent, sorted(row["evidence_id"] for row in newer)])
        yield {
            "event_id": f"{parent}:followup:{key[:16]}", "parent_event_id": parent,
            "ticker": enrollment["ticker"], "version_sha256": key,
            "original_contract_only": enrollment["contract_only"],
            "summary": packet["summary"], "company_evidence": newer,
            "frozen_at_utc": now.isoformat(),
        }


def prepare(directory, cohort, contracts, now):
    source_rows = list(company_rows())
    candidates = (insider_candidates(source_rows, now) if cohort == "insider"
                  else later_candidates(source_rows, contracts, now))
    path = directory / "candidates.txt"
    enrolled = {row["event_id"] for row in read_lines(directory / "enrollments.txt")}
    already = list(read_lines(path))
    new = list(candidates)
    if cohort == "followup":
        # Create a new observation only when at least one genuinely new source version appears.
        seen = {(r["parent_event_id"], item["evidence_id"])
                for r in already for item in r["company_evidence"]}
        new = [r for r in new if any((r["parent_event_id"], item["evidence_id"]) not in seen
                                    for item in r["company_evidence"])]
    added, _ = merge_records(path, new)
    pending = [r for r in read_lines(path) if r["event_id"] not in enrolled
               and 0 <= (now - time_utc(r["frozen_at_utc"])).total_seconds()
               <= MAX_BATCH_AGE.total_seconds()
               and (cohort != "insider" or
                    now - time_utc(r["summary"]["accepted_at_utc"]) <= timedelta(days=FRESH_DAYS))]
    if cohort == "insider":
        write_json_lines(directory / "insider_only.txt", (
            {"event_id": r["event_id"], "ticker": r["ticker"], "filing": r["summary"]}
            for r in pending
        ))
    write_json_lines(directory / "with_context.txt", (
        {"event_id": r["event_id"], "ticker": r["ticker"],
         "filing" if cohort == "insider" else "contract": r["summary"],
         "company_evidence": r["company_evidence"]}
        for r in pending if r["company_evidence"]
    ))
    print(f"{cohort}: {added} new candidates, {len(pending)} awaiting scores")


def create_enrollments(candidates, existing, baseline, contextual, cohort, now):
    ids = baseline.keys() if cohort == "insider" else contextual.keys()
    if ids - candidates.keys() or contextual.keys() - ids:
        raise ValueError("Unmatched evidence score IDs")
    entry, opens, exits = entry_window(now)
    rows = []
    for event in ids:
        if event in existing:
            continue
        row = candidates[event]
        if not 0 <= (now - time_utc(row["frozen_at_utc"])).total_seconds() <= MAX_BATCH_AGE.total_seconds():
            raise ValueError(f"Scoring window expired for {event}")
        if cohort == "insider" and now - time_utc(
            row["summary"]["accepted_at_utc"],
        ) > timedelta(days=FRESH_DAYS):
            raise ValueError(f"Form 4 freshness window expired for {event}")
        score = contextual.get(event)
        evidence = {item["evidence_id"]: item for item in row["company_evidence"]}
        check_context(event, evidence, score)
        relations = score.get("evidence_relations", {}) if score else {}
        if cohort == "followup" and not relations:
            raise ValueError(f"Score must label each cited follow-up as corroborates, challenges or context_only: {event}")
        same_award = bool(score and any(evidence[key]["award_id_mentioned"]
                                       for key in score["evidence_ids"]))
        common = {
            "version_sha256": row["version_sha256"], "event_id": event, "ticker": row["ticker"],
            "frozen_at_utc": row["frozen_at_utc"], "registered_at_utc": now.isoformat(),
            "scorer": "QuantLab Event Scoring (Codex)", "entry_session": entry,
            "entry_at_utc": opens, "exit_sessions": exits, "with_other_sources": score,
            "context_type": ("award_id_mentioned" if same_award else
                             "company_context" if score else "unavailable"),
        }
        if cohort == "insider":
            common.update(insider_only=baseline[event], transaction_side=row["summary"]["side"],
                          source_url=row["source_url"], source_version=row["source_version"])
        else:
            common.update(original_contract_only=row["original_contract_only"],
                          parent_event_id=row["parent_event_id"])
        rows.append(common)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cohort", choices=("insider", "followup"))
    parser.add_argument("--contracts", type=Path, default=CONTRACTS)
    parser.add_argument("--output-dir", type=Path)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("prepare")
    registration = modes.add_parser("register")
    registration.add_argument("--baseline", type=Path)
    registration.add_argument("--context", required=True, type=Path)
    modes.add_parser("report")
    args = parser.parse_args()
    directory = args.output_dir or ROOT / f"forward_{args.cohort}_test"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with output_lock(directory):
            now = datetime.now(UTC)
            if args.mode == "prepare":
                prepare(directory, args.cohort, args.contracts, now)
            elif args.mode == "register":
                if args.cohort == "insider" and args.baseline is None:
                    raise ValueError("Insider registration requires --baseline")
                candidates = {row["event_id"]: row for row in read_lines(directory / "candidates.txt")}
                path = directory / "enrollments.txt"
                existing = {row["event_id"] for row in read_lines(path)}
                baseline = load_scores(args.baseline) if args.baseline else {}
                contextual = load_scores(args.context, contextual=True)
                rows = create_enrollments(candidates, existing, baseline, contextual, args.cohort, now)
                added, total = merge_records(path, rows)
                print(f"{args.cohort}: {added} new enrollments, {total} total")
            else:
                arm = "insider_only" if args.cohort == "insider" else "original_contract_only"
                measure(directory, now, baseline_arm=arm)
    except (OSError, RuntimeError, KeyError, ValueError, TypeError) as error:
        parser.exit(1, f"Forward evidence test failed: {error}\n")


if __name__ == "__main__":
    main()
