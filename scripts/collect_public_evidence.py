"""Collect public watchlist corroboration into JSON Lines text, with no model calls."""

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

from collect_company_evidence import (
    AGENT,
    SOURCE_ERRORS,
    WATCHLIST,
    evidence,
    parse_time,
    short_text,
)
from collect_contract_announcements import fetch, record, utc
from collect_usaspending import merge_records, output_lock
from score_contract_events import ROOT, issuer, read_lines

OUTPUT = ROOT / "public_evidence"
FEDERAL = "https://www.federalregister.gov/api/v1/documents.json"
GAO = "https://www.gao.gov/rss/reportslegal.xml"
FINLAND = "https://valtioneuvosto.fi/staattiset-feedit/-/asset_publisher/6KLhMPTNqHRd/rss"
SUBAWARDS = "https://api.usaspending.gov/api/v2/subawards/"
GLEIF = "https://api.gleif.org/api/v1/lei-records"
TERMS = {"LMT": ("Lockheed Martin",), "NOC": ("Northrop Grumman",),
         "RTX": ("Pratt & Whitney", "Raytheon", "RTX Corporation", "Collins Aerospace")}


def name_matches(ticker, text):
    return any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I)
               for term in TERMS[ticker])


def federal(ticker, since, until):
    for term in TERMS[ticker]:
        parameters = {
            "conditions[term]": f'"{term}"', "conditions[publication_date][gte]": since,
            "conditions[publication_date][lte]": until, "order": "newest", "per_page": 100,
            "fields[]": ("document_number", "title", "type", "publication_date", "effective_on",
                         "abstract", "html_url", "agencies"),
        }
        for page in range(1, 11):
            url = FEDERAL + "?" + urlencode({**parameters, "page": page}, doseq=True)
            result, _ = fetch(url, json.load, user_agent=AGENT)
            for item in result["results"]:
                title, abstract = item["title"], item.get("abstract") or ""
                if not name_matches(ticker, title + " " + abstract):
                    continue
                # Federal Register dates have day precision. Do not invent a release time.
                yield record(
                    "federal_register", f"{ticker}:{item['document_number']}", item["html_url"],
                    {"headline": title, "brief": short_text(abstract, 400),
                     "document_type": item["type"], "publication_date": item["publication_date"],
                     "effective_date": item.get("effective_on")},
                    ticker=ticker, issuer=WATCHLIST[ticker][0], event_type="regulatory_notice",
                    published_at_utc=None, first_observed_at_utc=utc(datetime.now(UTC)),
                )
            if not result.get("next_page_url"):
                break
        else:
            raise ValueError(f"Federal Register search exceeded ten pages for {term}")


def rss_evidence(url, source, kind, since, until):
    root, _ = fetch(url, lambda stream: ElementTree.parse(stream).getroot(), user_agent=AGENT)
    items = root.findall("./channel/item")
    if not items:
        raise ValueError("Expected a nonempty RSS channel")
    observed = datetime.now(UTC)
    for item in items:
        title = short_text(item.findtext("title"), 240)
        brief = short_text(item.findtext("description"), 400)
        stamp = item.findtext("pubDate")
        link = item.findtext("link")
        if not stamp or not link or not link.startswith("https://"):
            continue
        when = parse_time(stamp)
        if not since <= when.date().isoformat() <= until:
            continue
        if source == "gao_legal" and not re.search(r"\b(?:protest|solicitation|award)\b", brief, re.I):
            continue
        for ticker in WATCHLIST:
            if name_matches(ticker, title + " " + brief):
                yield evidence(ticker, source, f"{ticker}:{link}", link, kind, when, observed,
                               headline=title, brief=brief)


def foreign_sales(since, until):
    parameters = {
        "conditions[term]": '"Arms Sales Notification"', "conditions[publication_date][gte]": since,
        "conditions[publication_date][lte]": until, "order": "newest", "per_page": 60,
    }
    for page in range(1, 6):
        result, _ = fetch(FEDERAL + "?" + urlencode({**parameters, "page": page}),
                          json.load, user_agent=AGENT)
        for row in result["results"]:
            if row["title"].strip().casefold() != "arms sales notification":
                continue
            identifier, day = row["document_number"], row["publication_date"]
            if not re.fullmatch(r"\d{4}-\d{5}", identifier) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                raise ValueError("Unexpected Federal Register notice identity")
            link = f"https://www.govinfo.gov/content/pkg/FR-{day}/html/{identifier}.htm"
            page_html, _ = fetch(link, lambda stream: stream.read().decode("utf-8", "replace"),
                                 user_agent=AGENT)
            body = short_text(page_html, 300000)
            buyer = re.search(r"Prospective Purchaser:\s*(.+?)\s*\(ii\)", body, re.I)
            estimated = re.search(r"TOTAL\.*\s*\$\s*([\d,.]+\s*(?:million|billion))", body, re.I)
            principal = re.search(
                r"The principal contractors?\s+(?:will be|is|are)\s+(.+?)(?=\.\s+"
                r"(?:At this time|There are|There will|Implementation|The U\.S\.)|$)", body, re.I,
            )
            if not principal:
                continue
            company = principal[1][:400]
            for ticker in WATCHLIST:
                if not name_matches(ticker, company):
                    continue
                yield record(
                    "govinfo_foreign_sales", f"{ticker}:{identifier}", link,
                    {"headline": "Proposed Foreign Military Sale", "publication_date": day,
                     "prospective_buyer": buyer[1][:160] if buyer else None,
                     "named_principal_contractors": company,
                     "estimated_package_value_usd": estimated[1] if estimated else None,
                     "stage": "congressional_notification_of_possible_sale"},
                    ticker=ticker, issuer=WATCHLIST[ticker][0], event_type="possible_foreign_sale",
                    published_at_utc=None, first_observed_at_utc=utc(datetime.now(UTC)),
                )
        if not result.get("next_page_url"):
            return
    raise ValueError("Foreign sales search exceeded five pages")


def subawards(spending, since, until):
    # Search subawards only for known watchlist prime awards. This avoids an unbounded API crawl.
    primes = {}
    for path in sorted(spending.glob("*.txt"), reverse=True)[:30]:
        for row in read_lines(path):
            raw = row["source_record"]
            mapping = issuer(raw.get("Recipient Name") or "")
            if mapping and raw.get("generated_internal_id"):
                primes.setdefault(raw["generated_internal_id"], (mapping[0], raw["Award ID"]))
    for prime, (ticker, award) in list(primes.items())[:25]:
        for page in range(1, 11):
            result, _ = fetch(
                SUBAWARDS, json.load, user_agent=AGENT,
                payload={"award_id": prime, "limit": 100, "page": page,
                         "sort": "action_date", "order": "desc"},
            )
            rows = result["results"]
            for item in rows:
                day = item.get("action_date") or ""
                if not since <= day <= until:
                    continue
                number = item.get("subaward_number") or str(item["id"])
                recipient = item.get("recipient_name") or ""
                yield record(
                    "usaspending_subaward", f"{prime}:{number}:{recipient}",
                    f"https://www.usaspending.gov/award/{quote(prime, safe='')}/",
                    {"award_id": award, "subaward_number": number, "transaction_date": day,
                     "subrecipient": recipient, "subaward_amount_usd": item.get("amount"),
                     "brief": short_text(item.get("description"), 240)},
                    ticker=ticker, issuer=WATCHLIST[ticker][0], event_type="subaward",
                    published_at_utc=None, first_observed_at_utc=utc(datetime.now(UTC)),
                )
            if not result["page_metadata"]["hasNext"] or any(
                row.get("action_date") and row["action_date"] < since for row in rows
            ):
                break
        else:
            raise ValueError(f"Subaward search exceeded ten pages for {prime}")


def identities():
    for ticker, (name, cik, _) in WATCHLIST.items():
        url = GLEIF + "?" + urlencode({"filter[entity.legalName]": name, "page[size]": 10})
        response, _ = fetch(url, json.load, user_agent=AGENT)
        for item in response["data"]:
            attributes = item["attributes"]
            official = attributes["entity"]["legalName"]["name"]
            if re.sub(r"[^A-Z]", "", official.upper()) != re.sub(r"[^A-Z]", "", name.upper()):
                continue
            if attributes["entity"].get("status") != "ACTIVE":
                continue
            lei = item["id"]
            yield record(
                "gleif", lei, f"{GLEIF}/{lei}", {"ticker": ticker, "legal_name": official,
                                                "lei": lei, "sec_cik": cik},
                ticker=ticker, first_observed_at_utc=utc(datetime.now(UTC)),
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="all",
                        choices=("all", "federal", "gao", "finland", "foreign_sales",
                                 "subawards", "gleif"))
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--usaspending", type=Path, default=ROOT / "usaspending")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be positive")
    today = datetime.now(UTC).date()
    since, until = (today - timedelta(days=args.days)).isoformat(), today.isoformat()
    jobs = {
        "federal": lambda: (r for ticker in WATCHLIST for r in federal(ticker, since, until)),
        "gao": lambda: rss_evidence(GAO, "gao_legal", "oversight_notice", since, until),
        "finland": lambda: rss_evidence(FINLAND, "finnish_government", "buyer_announcement", since, until),
        "foreign_sales": lambda: foreign_sales(since, until),
        "subawards": lambda: subawards(args.usaspending, since, until),
        "gleif": identities,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    with output_lock(args.output_dir):
        for name, job in jobs.items():
            if args.source not in ("all", name):
                continue
            try:
                rows = list(job())
                path = args.output_dir / ("issuer_identities.txt" if name == "gleif" else f"{name}.txt")
                added, _ = merge_records(path, rows)
                print(f"{name}: {len(rows)} watchlist matches, {added} new versions")
            except SOURCE_ERRORS as error:
                print(f"{name}: {error}", file=sys.stderr)
                failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
