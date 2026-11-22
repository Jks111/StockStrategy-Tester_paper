"""Freeze and measure paired contract forecasts without placing orders."""

import argparse
import math
import re
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean

from collect_usaspending import merge_records, output_lock, write_json_lines
from score_contract_events import ROOT, digest, fresh, prepare, read_lines, validate_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quantlab.calendars import sessions
from quantlab.data.providers import NY, Alpaca

TICKERS = ("LMT", "NOC", "RTX")
BENCHMARK = "ITA"
HORIZONS = (1, 5, 20)
THRESHOLD = 8
FRESH_DAYS = 14
CONTEXT_DAYS = 45
MAX_BATCH_AGE = timedelta(days=2)
OUTPUT = ROOT / "forward_contract_test"


def time_utc(value):
    time = datetime.fromisoformat(value)
    if time.tzinfo is None:
        raise ValueError(f"Timestamp has no time zone: {value}")
    return time.astimezone(UTC)


def eligible_context(rows, ticker, award, now, after=None):
    recent = []
    for row in rows:
        if row["ticker"] != ticker or row["event_type"] == "capital_return":
            continue
        data = row["source_record"]
        observed = time_utc(row["first_observed_at_utc"])
        published = row["published_at_utc"] or data.get("accepted_at_utc")
        date_only = data.get("publication_date") or data.get("transaction_date")
        if observed > now or not (published or date_only):
            continue
        if after is not None and observed <= after:
            continue
        if published:
            released = time_utc(published)
            released_day = released.astimezone(NY).date()
            if released > observed or (after is not None and released <= after):
                continue
        else:
            released_day = datetime.fromisoformat(date_only).date()
            if released_day > observed.date() or (after is not None and released_day <= after.date()):
                continue
        if released_day < (now - timedelta(days=CONTEXT_DAYS)).date():
            continue
        # Issuer news about other contracts can inform context but cannot confirm this award.
        text = " ".join(data.get(field) or "" for field in ("headline", "brief"))
        pattern = r"(?<![A-Z0-9])" + r"[\s-]*".join(map(re.escape, award)) + r"(?![A-Z0-9])"
        matches_award = len(award) >= 8 and (
            re.sub(r"[^A-Z0-9]", "", (data.get("award_id") or "").upper()) == award
            or bool(re.search(pattern, text, re.I))
        )
        date = released_day.isoformat()
        category = row["event_type"]
        earnings = category in ("earnings", "periodic_report") or (
            category == "current_report" and "Results of operations" in data.get("filing_items", ())
        )
        if category == "current_report" and not matches_award and not earnings:
            continue  # Item headings alone do not add usable company context.
        group = f"{ticker}:{date}:earnings" if earnings else row["source_id"]
        context = {
            "evidence_id": row["version_sha256"],
            "source": row["source"],
            "source_url": row["source_url"],
            "first_observed_at_utc": row["first_observed_at_utc"],
            "released_at_utc": published,
            "reference_date": date,
            "event_type": category,
            "award_id_mentioned": matches_award,
            "summary": data,
        }
        priority = 2 if re.search(r"\b(?:guidance|outlook)\b", text, re.I) else int(
            category == "periodic_report",
        )
        recent.append((matches_award, released_day, priority, group, context))
    recent.sort(key=lambda row: row[:3], reverse=True)
    selected = {}
    for _, _, _, group, context in recent:
        selected.setdefault(group, context)
    return list(selected.values())[:4]


def company_rows():
    for path in [ROOT / "company_evidence" / "company_summaries.txt",
                 *sorted((ROOT / "public_evidence").glob("*.txt")),
                 ROOT / "insider_evidence" / "filing_summaries.txt"]:
        if path.name != "issuer_identities.txt":
            yield from read_lines(path)


def freeze_packets(directory, now):
    path = directory / "candidates.txt"
    existing = {row["event_id"] for row in read_lines(path)}
    candidates = prepare(
        ROOT / "contract_announcements" / "sam.txt", ROOT / "usaspending",
        Decimal("1000000"), 30, now.date(), FRESH_DAYS, False,
    )
    context, insiders = [], []
    for row in company_rows():
        (insiders if row["source"] == "sec_form4" else context).append(row)
    new = []
    for row in candidates:
        summary = row["summary"]
        if summary["ticker"] not in TICKERS or row["event_id"] in existing:
            continue
        if any(time_utc(item["first_observed_at_utc"]) > now for item in row["provenance"]):
            continue
        new.append({
            **row,
            "version_sha256": digest(["forward-event", row["event_id"]]),
            "contract_summary_id": row["version_sha256"],
            "frozen_at_utc": now.isoformat(),
            "company_evidence": eligible_context(context, summary["ticker"], summary["award_id"], now),
            "insider_evidence": eligible_context(insiders, summary["ticker"], "", now)[:2],
        })
    added, _ = merge_records(path, new)
    enrolled = {row["event_id"] for row in read_lines(directory / "enrollments.txt")}
    pending = [row for row in read_lines(path)
               if row["event_id"] not in enrolled
               and now - time_utc(row["frozen_at_utc"]) <= MAX_BATCH_AGE
               and any(fresh(item, now.date(), FRESH_DAYS) for item in row["summary"]["evidence"])]
    write_json_lines(directory / "contract_only.txt", (
        {"event_id": row["event_id"], "contract": row["summary"]} for row in pending
    ))
    write_json_lines(directory / "with_context.txt", (
        {"event_id": row["event_id"], "contract": row["summary"],
         "company_evidence": row["company_evidence"] + row.get("insider_evidence", [])}
        for row in pending if row["company_evidence"] or row.get("insider_evidence")
    ))
    write_json_lines(directory / "without_insider.txt", (
        {"event_id": row["event_id"], "contract": row["summary"],
         "company_evidence": row["company_evidence"]}
        for row in pending if row["company_evidence"] and row.get("insider_evidence")
    ))
    print(f"Frozen {added} new events. {len(pending)} awaiting chat scores.")


def load_scores(path, contextual=False):
    scores = {}
    for row in read_lines(path, required=True):
        event = row.pop("event_id")
        if event in scores:
            raise ValueError(f"Duplicate score for {event}")
        if contextual:
            ids = row.pop("evidence_ids")
            if (not isinstance(ids, list) or not ids or
                    any(not isinstance(key, str) for key in ids) or len(set(ids)) != len(ids)):
                raise ValueError(f"Expected distinct evidence IDs for {event}")
            relations = row.pop("evidence_relations", None)
            if relations is not None and (
                not isinstance(relations, dict) or set(relations) != set(ids)
                or any(value not in ("corroborates", "challenges", "context_only")
                       for value in relations.values())
            ):
                raise ValueError(f"Invalid evidence relationships for {event}")
            scores[event] = {**validate_score(row), "evidence_ids": ids}
            if relations is not None:
                scores[event]["evidence_relations"] = relations
        else:
            scores[event] = validate_score(row)
    return scores


def entry_window(available):
    start = available.astimezone(NY).date()
    exchange = sessions("XNYS", start.isoformat(), (start + timedelta(days=70)).isoformat())
    upcoming = [(day, open_time, close) for day, (open_time, close) in sorted(exchange.items())
                if open_time > available]
    entry, open_time, _ = upcoming[0]
    return entry, open_time.isoformat(), {str(n): upcoming[n - 1][0] for n in HORIZONS}


def check_context(event, evidence, score):
    if bool(evidence) != bool(score) or (score and set(score["evidence_ids"]) - evidence.keys()):
        raise ValueError(f"Context score or cited evidence does not match the frozen packet for {event}")
    if score and any(
        relation == "corroborates" and not evidence[key]["award_id_mentioned"]
        for key, relation in score.get("evidence_relations", {}).items()
    ):
        raise ValueError(f"Same-award corroboration needs a matching award ID for {event}")


def enroll(candidates, already, baseline, contextual, now, without_insider=None):
    without_insider = without_insider or {}
    if baseline.keys() - candidates.keys() or (contextual.keys() | without_insider.keys()) - baseline.keys():
        raise ValueError("Score files contain unknown or unmatched event IDs")
    entry, opens, exits = entry_window(now)
    rows = []
    for event, base in baseline.items():
        if event in already:
            continue
        packet = candidates[event]
        frozen = time_utc(packet["frozen_at_utc"])
        if not 0 <= (now - frozen).total_seconds() <= MAX_BATCH_AGE.total_seconds() or not any(
            fresh(item, now.date(), FRESH_DAYS) for item in packet["summary"]["evidence"]
        ):
            raise ValueError(f"Scoring window expired for {event}")
        others = {row["evidence_id"]: row for row in packet["company_evidence"]}
        insider = {row["evidence_id"]: row for row in packet.get("insider_evidence", [])}
        combined = others | insider
        context = contextual.get(event)
        check_context(event, combined, context)
        check_context(event, others if insider else {}, without_insider.get(event))
        same_award = bool(context and any(combined[key]["award_id_mentioned"]
                                         for key in context["evidence_ids"]))
        row = {
            "version_sha256": packet["version_sha256"],
            "event_id": event,
            "ticker": packet["summary"]["ticker"],
            "contract_summary_id": packet["contract_summary_id"],
            "frozen_at_utc": packet["frozen_at_utc"],
            "registered_at_utc": now.isoformat(),
            "scorer": "QuantLab Event Scoring (Codex)",
            "entry_session": entry,
            "entry_at_utc": opens,
            "exit_sessions": exits,
            "contract_only": base,
            "with_other_sources": context,
            "context_type": ("award_id_mentioned" if same_award else
                             "company_context" if context else "unavailable"),
        }
        if insider:
            row["without_insider"] = without_insider.get(event, base)
        rows.append(row)
    return rows


def buy(score):
    return bool(score and score["score"] >= THRESHOLD and score["direction"] == "positive"
                and score["confidence"] == "high")


def measure(directory, now, baseline_arm="contract_only"):
    tracked = list(read_lines(directory / "enrollments.txt"))
    path = directory / "returns.txt"
    existing = {row["version_sha256"] for row in read_lines(path)}
    cutoff = now - timedelta(minutes=16)
    due = []
    for row in tracked:
        exchange = sessions("XNYS", row["entry_session"], row["exit_sessions"]["20"])
        for horizon, exit_day in row["exit_sessions"].items():
            key = digest([row["event_id"], horizon])
            if key not in existing and exchange[exit_day][1] <= cutoff:
                due.append((row, horizon, exit_day, key))
    if due:
        start = min(row["entry_session"] for row, *_ in due)
        end = max(day for _, _, day, _ in due)
        symbols = sorted({BENCHMARK, *(row["ticker"] for row, *_ in due)})
        bars = Alpaca().history(symbols, start, end, "1Day", "sip", cutoff=cutoff)
        prices = {(bar.symbol, bar.session): bar for bar in bars}
        merge_records(path, observed_returns(due, prices, now))
    else:
        merge_records(path, [])
    results = list(read_lines(path))
    write_json_lines(directory / "comparison.txt", compare(tracked, results, baseline_arm))
    print(f"{len(tracked)} enrolled events, {len(results)} completed event horizons.")


def observed_returns(due, bars, now):
    for row, horizon, exit_day, key in due:
        ticker, entry = row["ticker"], row["entry_session"]
        start, end, sector_start, sector_end = (
            bars.get(pair) for pair in ((ticker, entry), (ticker, exit_day),
                                        (BENCHMARK, entry), (BENCHMARK, exit_day))
        )
        if any(bar is None for bar in (start, end, sector_start, sector_end)):
            print(f"{row['event_id']} {horizon} sessions: missing stock or ITA bar", file=sys.stderr)
            continue
        prices = start.open, end.close, sector_start.open, sector_end.close
        if any(not math.isfinite(price) or price <= 0 for price in prices):
            raise ValueError(f"Invalid market prices for {row['event_id']}")
        stock = prices[1] / prices[0] - 1
        sector = prices[3] / prices[2] - 1
        yield {
            "version_sha256": key, "event_id": row["event_id"], "ticker": ticker,
            "horizon_sessions": int(horizon), "entry_session": entry, "exit_session": exit_day,
            "benchmark": BENCHMARK,
            "stock_open": prices[0], "stock_close": prices[1],
            "benchmark_open": prices[2], "benchmark_close": prices[3],
            "stock_return_pct": round(100 * stock, 6), "benchmark_return_pct": round(100 * sector, 6),
            "excess_return_pct": round(100 * (stock - sector), 6),
            "observed_at_utc": now.isoformat(),
        }


def metrics(rows, arm):
    selected = [result for event, result in rows if buy(event[arm])]
    excess = [result["excess_return_pct"] for result in selected]
    by_score = {}
    for score in sorted({event[arm]["score"] for event, _ in rows}):
        values = [r["excess_return_pct"] for e, r in rows if e[arm]["score"] == score]
        by_score[str(score)] = {"count": len(values), "mean_excess_pct": round(mean(values), 6)}
    return {
        "completed": len(rows), "buy_count": len(selected),
        "buy_mean_excess_pct": round(mean(excess), 6) if excess else None,
        "buys_outperforming_ita": sum(value > 0 for value in excess),
        "by_score": by_score,
    }


def compare(enrolled, returns, baseline_arm="contract_only"):
    lookup = {event["event_id"]: event for event in enrolled}
    for n in HORIZONS:
        complete = [(lookup[row["event_id"]], row) for row in returns if row["horizon_sessions"] == n]
        paired = [(event, row) for event, row in complete if event["with_other_sources"]]
        linked = [(event, row) for event, row in paired if event["context_type"] == "award_id_mentioned"]
        report = {
            "horizon_sessions": n, "benchmark": BENCHMARK, "threshold": THRESHOLD,
            "enrolled": len(enrolled), "pending": len(enrolled) - len(complete),
            f"registered_{baseline_arm}_buys": sum(buy(event[baseline_arm]) for event in enrolled),
            "registered_with_other_sources_buys": sum(buy(event["with_other_sources"]) for event in enrolled),
            f"{baseline_arm}_all": metrics(complete, baseline_arm),
            f"paired_{baseline_arm}": metrics(paired, baseline_arm),
            "paired_with_other_sources": metrics(paired, "with_other_sources"),
            "paired_same_award_mentions": len(linked),
            f"same_award_{baseline_arm}": metrics(linked, baseline_arm),
            "same_award_with_other_sources": metrics(linked, "with_other_sources"),
            "paired_stock_entry_windows": len({(e["ticker"], e["entry_session"]) for e, _ in paired}),
            "paired_mean_score_change": round(mean(
                e["with_other_sources"]["score"] - e[baseline_arm]["score"] for e, _ in paired
            ), 6) if paired else None,
            "paired_decisions_changed": sum(buy(e[baseline_arm]) != buy(e["with_other_sources"])
                                            for e, _ in paired),
        }
        if baseline_arm == "insider_only":
            report["by_transaction_side"] = {
                side: metrics([(e, r) for e, r in complete if e["transaction_side"] == side],
                              baseline_arm)
                for side in ("purchase", "sale", "mixed")
            }
        if baseline_arm == "contract_only":
            registered = [event for event in enrolled if event.get("without_insider")]
            matched = [(event, row) for event, row in paired if event.get("without_insider")]
            report["incremental_insider"] = {
                "enrolled": len(registered),
                "pending": len(registered) - len(matched),
                "registered_decisions_changed": sum(
                    buy(e["without_insider"]) != buy(e["with_other_sources"]) for e in registered
                ),
                "without_insider": metrics(matched, "without_insider"),
                "with_insider": metrics(matched, "with_other_sources"),
                "mean_score_change": round(mean(
                    e["with_other_sources"]["score"] - e["without_insider"]["score"] for e, _ in matched
                ), 6) if matched else None,
            }
        yield report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("prepare", help="Freeze candidates from the token-free collectors")
    registration = modes.add_parser("register", help="Register results from the scoring chat")
    registration.add_argument("--contract-scores", required=True, type=Path)
    registration.add_argument("--context-scores", required=True, type=Path)
    registration.add_argument("--without-insider-scores", type=Path)
    modes.add_parser("report", help="Fetch completed Alpaca bars and update results")
    args = parser.parse_args()
    directory = args.output_dir
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with output_lock(directory):
            now = datetime.now(UTC)
            if args.mode == "prepare":
                freeze_packets(directory, now)
            elif args.mode == "register":
                candidates = {row["event_id"]: row for row in read_lines(directory / "candidates.txt", required=True)}
                path = directory / "enrollments.txt"
                existing = {row["event_id"] for row in read_lines(path)}
                baseline = load_scores(args.contract_scores)
                contextual = load_scores(args.context_scores, contextual=True)
                without_insider = (load_scores(args.without_insider_scores, contextual=True)
                                   if args.without_insider_scores else {})
                rows = enroll(candidates, existing, baseline, contextual, now, without_insider)
                added, total = merge_records(path, rows)
                print(f"{added} new forward enrollments, {total} total.")
            else:
                measure(directory, now)
    except (OSError, RuntimeError, KeyError, ValueError, TypeError) as error:
        parser.exit(1, f"Forward test failed: {error}\n")


if __name__ == "__main__":
    main()
