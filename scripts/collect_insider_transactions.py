"""Save SEC Form 4 P/S transaction evidence as plain-text JSON Lines."""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from xml.etree import ElementTree

from collect_company_evidence import AGENT, SEC, SOURCE_ERRORS, WATCHLIST, parse_time
from collect_contract_announcements import fetch, record, utc
from collect_usaspending import merge_records, output_lock, write_json_lines
from score_contract_events import ROOT, money, read_lines

OUTPUT = ROOT / "insider_evidence"


def bounded_xml(response):
    return response.read(5_000_001)


def ownership_document(data, ticker, forms):
    if len(data) > 5_000_000 or re.search(br"<!DOCTYPE|<!ENTITY", data, re.I):
        raise ValueError("Invalid or oversized ownership XML")
    doc = ElementTree.fromstring(data)
    symbol = doc.findtext("issuer/issuerTradingSymbol", "").upper().strip()
    form = doc.findtext("documentType", "")
    if doc.tag != "ownershipDocument" or symbol != ticker or form not in forms:
        raise ValueError("Unexpected ownership filing issuer or form")
    return doc


def ownership_urls(cik, accession, name):
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not re.fullmatch(
        r"(?:[\w-]+/)?[\w.-]+\.xml", name, re.I,
    ):
        raise ValueError(f"Unexpected ownership filing link for {accession}")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    return base + quote(name, safe="/"), base + quote(name.rsplit("/", 1)[-1])


def reporting_owners(doc):
    owners = []
    for owner in doc.findall("reportingOwner"):
        relation = owner.find("reportingOwnerRelationship")
        title = relation.findtext("officerTitle", "") if relation is not None else ""
        roles = [label for tag, label in (("isDirector", "director"), ("isOfficer", "officer"),
                                         ("isTenPercentOwner", "10% owner"))
                 if relation is not None and relation.findtext(tag, "").lower() in ("1", "true")]
        owners.append({"name": owner.findtext("reportingOwnerId/rptOwnerName", ""),
                       "roles": roles, "officer_title": title})
    return owners


def parse_transaction_filing(data, ticker, accession, url, accepted, observed):
    doc = ownership_document(data, ticker, {"4", "4/A"})
    form = doc.findtext("documentType")
    owners = reporting_owners(doc)
    flag = doc.findtext("aff10b5One", "").lower()
    plan = True if flag in ("1", "true") else False if flag in ("0", "false") else None
    for index, tx in enumerate(doc.findall("nonDerivativeTable/nonDerivativeTransaction")):
        code = tx.findtext("transactionCoding/transactionCode", "")
        side = tx.findtext("transactionAmounts/transactionAcquiredDisposedCode/value", "")
        if (code, side) not in (("P", "A"), ("S", "D")):
            continue
        shares = money(tx.findtext("transactionAmounts/transactionShares/value"))
        price = money(tx.findtext("transactionAmounts/transactionPricePerShare/value"))
        tx_date = tx.findtext("transactionDate/value")
        if not tx_date or shares is None or shares <= 0:
            continue
        price = price if price is not None and price > 0 else None
        kind = "insider_purchase" if code == "P" else "insider_sale"
        yield record(
            "sec_form4", f"{accession}:{index}", url,
            {"accession": accession, "form": form, "transaction_date": tx_date,
             "accepted_at_utc": utc(accepted), "transaction_code": code,
             "security": tx.findtext("securityTitle/value", ""), "reporting_owners": owners,
             "filing_indicates_10b5_1_plan": plan,
             "ownership": tx.findtext("ownershipNature/directOrIndirectOwnership/value", ""),
             "shares": format(shares, "f"), "price_per_share_usd": format(price, "f") if price else None,
             "reported_value_usd": format(shares * price, "f") if price else None},
            ticker=ticker, issuer=WATCHLIST[ticker][0], event_type=kind, published_at_utc=None,
            first_observed_at_utc=utc(observed),
        )


def collect(ticker, days, directory, path, checked_path, agent, now):
    cik = WATCHLIST[ticker][1]
    result, _ = fetch(f"{SEC}submissions/CIK{cik}.json", json.load, user_agent=agent)
    recent = result["filings"]["recent"]
    rows = zip(*(recent[key] for key in ("form", "accessionNumber", "acceptanceDateTime",
                                         "primaryDocument")), strict=True)
    checked = {r["accession"] for r in read_lines(checked_path)}
    successes = blocked = found = 0
    for form, accession, accepted, name in rows:
        if form not in ("4", "4/A") or accession in checked:
            continue
        when = parse_time(accepted)
        if not now - timedelta(days=days) <= when <= now:
            continue
        source, raw_url = ownership_urls(cik, accession, name)
        local = directory / (accession + ".xml") if directory else None
        try:
            if local and local.is_file():
                with local.open("rb") as stream:
                    data = bounded_xml(stream)
            else:
                data, _ = fetch(raw_url, bounded_xml, user_agent=agent)
            results = list(parse_transaction_filing(
                data, ticker, accession, source, when, datetime.now(UTC),
            ))
        except HTTPError as error:
            print(f"{ticker} {accession}: raw SEC filing unavailable (HTTP {error.code})", file=sys.stderr)
            blocked += 1
            continue
        finally:
            time.sleep(0.25)
        added, _ = merge_records(path, results)
        merge_records(checked_path, [{"version_sha256": accession, "accession": accession,
                                      "ticker": ticker, "source_url": source,
                                      "checked_at_utc": utc(datetime.now(UTC)), "matched_rows": len(results)}])
        successes += 1
        found += added
    print(f"{ticker}: {successes} new filings checked, {found} new P/S rows, {blocked} unavailable")
    return blocked


def filing_summaries(rows):
    filings = defaultdict(list)
    for row in rows:
        filings[row["ticker"], row["source_record"]["accession"]].append(row)
    for (ticker, accession), transactions in sorted(filings.items()):
        first = transactions[0]
        data = first["source_record"]
        details = [row["source_record"] for row in transactions]
        codes = {tx["transaction_code"] for tx in details}
        side = "mixed" if len(codes) > 1 else "purchase" if codes == {"P"} else "sale"
        trades = [{field: tx[field] for field in (
            "transaction_date", "transaction_code", "security", "ownership", "shares",
            "price_per_share_usd", "reported_value_usd",
        )} for tx in details]
        yield record(
            "sec_form4", accession, first["source_url"],
            {"accession": accession, "form": data["form"], "accepted_at_utc": data["accepted_at_utc"],
             "side": side, "reporting_owners": data["reporting_owners"],
             "filing_indicates_10b5_1_plan": data["filing_indicates_10b5_1_plan"],
             "transactions": trades},
            ticker=ticker, issuer=first["issuer"], event_type=f"insider_{side}",
            published_at_utc=None,
            first_observed_at_utc=max(row["first_observed_at_utc"] for row in transactions),
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", action="append", choices=WATCHLIST)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--sec-contact", help="Operator contact for the SEC User-Agent")
    parser.add_argument("--xml-dir", type=Path, help="Optional folder of SEC XML named by accession")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    agent = f"{AGENT} {args.sec_contact}" if args.sec_contact else AGENT
    now = datetime.now(UTC)
    failed = False
    with output_lock(args.output_dir):
        path = args.output_dir / "insider_transactions.txt"
        checked = args.output_dir / "checked_filings.txt"
        merge_records(path, [])
        for ticker in dict.fromkeys(args.ticker or WATCHLIST):
            try:
                failed |= bool(collect(ticker, args.days, args.xml_dir, path, checked, agent, now))
            except SOURCE_ERRORS as error:
                print(f"{ticker} insider filings: {error}", file=sys.stderr)
                failed = True
        write_json_lines(args.output_dir / "filing_summaries.txt", filing_summaries(read_lines(path)))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
