"""Save recent SEC and issuer disclosures as short, unscored JSON Lines summaries."""

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree

from collect_contract_announcements import fetch, record, utc
from collect_usaspending import merge_records, output_lock

WATCHLIST = {
    "LMT": ("Lockheed Martin Corporation", "0000936468", (
        "https://news.lockheedmartin.com/news-releases?pagetemplate=rss&category=788",
        "https://news.lockheedmartin.com/news-releases?pagetemplate=rss",
    )),
    "NOC": ("Northrop Grumman Corporation", "0001133421", ("https://news.northropgrumman.com/",)),
    "RTX": ("RTX Corporation", "0000101829", ("https://www.rtx.com/rss-feeds/news",)),
}
OUTPUT = Path(__file__).resolve().parents[1] / "var" / "company_evidence"
SEC = "https://data.sec.gov/"
AGENT = "QuantLab Company Evidence/1.0"
FORMS = {"8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"}
ITEMS = {
    "1.01": "Material definitive agreement", "1.02": "Termination of material agreement",
    "1.03": "Bankruptcy or receivership", "1.05": "Material cybersecurity incident",
    "2.01": "Completed acquisition or disposition", "2.02": "Results of operations",
    "2.03": "Direct financial obligation", "2.04": "Early or increased financial obligation",
    "2.05": "Exit or disposal costs", "2.06": "Material impairment",
    "3.01": "Listing compliance or delisting notice", "3.02": "Unregistered equity sale",
    "4.01": "Change in auditor", "4.02": "Non-reliance on financial statements",
    "5.01": "Change in control", "5.02": "Director, officer or compensation changes",
    "7.01": "Regulation FD disclosure", "8.01": "Other events",
}
METRICS = {
    "revenue": (("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                 "Revenues"), "USD"),
    "net_income": (("NetIncomeLoss", "ProfitLoss"), "USD"),
    "diluted_eps": (("EarningsPerShareDiluted",), "USD/shares"),
}
NEWS_TYPES = {
    "earnings": r"\b(?:results|earnings)\b",
    "guidance": r"\b(?:guidance|outlook|backlog)\b",
    "acquisition": r"\b(?:acquir\w+|acquisition|merger|divest\w+|sale of)\b",
    "capital_return": r"\b(?:dividend|repurchases?|buybacks?)\b",
    "business_award": r"\b(?:awards?|awarded|contracts?|framework agreement|\$[\d,.]+\s+(?:billion|million)\b.{0,20}\border)\b",
}
NEWS_SKIP = re.compile(
    r"\b(?:to (?:release|report|announce)\b.*\b(?:results|earnings)|"
    r"to (?:present|speak|participate)\b.*\bconference)\b", re.I,
)
SOURCE_ERRORS = (OSError, HTTPException, ValueError, KeyError, TypeError, ElementTree.ParseError)


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def short_text(html, limit):
    parser = Text()
    parser.feed(html or "")
    text = " ".join(" ".join(parser.parts).split())
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def parse_time(text):
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        value = parsedate_to_datetime(text)
    if value.tzinfo is None:
        raise ValueError(f"No time zone supplied in publication timestamp: {text}")
    return value


def evidence(ticker, source, key, link, event_type, published, observed, **summary):
    published_at = utc(published) if published is not None else None
    return record(
        source, key, link,
        {"ticker": ticker, "event_type": event_type, "published_at_utc": published_at, **summary},
        ticker=ticker, issuer=WATCHLIST[ticker][0], event_type=event_type,
        published_at_utc=published_at, first_observed_at_utc=utc(observed),
    )


def financial_metrics(facts, accession, period_end, form):
    if not period_end:
        return {}
    end = datetime.fromisoformat(period_end).date()
    min_days, max_days = (330, 400) if form.startswith("10-K") else (75, 110)
    results = {}
    for metric, (tags, unit) in METRICS.items():
        for tag in tags:
            values = facts.get(tag, {}).get("units", {}).get(unit, ())
            matches = [value for value in values
                       if value.get("accn") == accession and value.get("end") == period_end
                       and value.get("start") and min_days <= (
                           end - datetime.fromisoformat(value["start"]).date()
                       ).days <= max_days]
            if matches:
                value = matches[-1]
                results[metric] = {key: value[key] for key in ("start", "end", "val")} | {"unit": unit}
                break
    return results


def sec_records(ticker, cutoff, observed, agent):
    cik = WATCHLIST[ticker][1]
    filings, _ = fetch(f"{SEC}submissions/CIK{cik}.json", json.load, user_agent=agent)
    recent = filings["filings"]["recent"]
    fields = ("accessionNumber", "form", "items", "acceptanceDateTime", "reportDate", "primaryDocument")
    selected = []
    for accession, form, items, accepted, period, document in zip(
        *(recent[field] for field in fields), strict=True,
    ):
        if form not in FORMS:
            continue
        labels = [ITEMS[item] for item in (part.strip() for part in items.split(","))
                  if item in ITEMS]
        if form.startswith("8-K") and not labels:
            continue
        published = parse_time(accepted)
        if not cutoff <= published <= observed:
            continue
        selected.append((accession, form, labels, published, period, document))

    facts = {}
    warning = False
    if any(form.startswith(("10-K", "10-Q")) for _, form, *_ in selected):
        try:
            result, _ = fetch(
                f"{SEC}api/xbrl/companyfacts/CIK{cik}.json", json.load, user_agent=agent,
            )
            facts = result["facts"]["us-gaap"]
        except SOURCE_ERRORS as error:
            print(f"{ticker} SEC financial facts: {error}. Saving filing summaries.", file=sys.stderr)
            warning = True

    records = []
    collected = datetime.now(UTC)
    for accession, form, labels, published, period, document in selected:
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
        kind = "current_report" if form.startswith("8-K") else "periodic_report"
        summary = {"form": form, "report_date": period or None, "accepted_at_utc": utc(published)}
        if labels:
            summary["filing_items"] = labels
        if kind == "periodic_report" and facts:
            metrics = financial_metrics(facts, accession, period, form)
            if metrics:
                summary["financial_facts"] = metrics
        records.append(evidence(
            ticker, "sec_edgar", accession, url + quote(document), kind, None, collected, **summary,
        ))
    return records, warning


def rss_items(response, feed):
    channel = ElementTree.parse(response).getroot().find("channel")
    if channel is None or not channel.findall("item"):
        raise ValueError("Expected an issuer RSS channel with news items")
    for item in channel.findall("item"):
        title = item.findtext("title", "")
        link = urljoin(feed, item.findtext("link", ""))
        yield link, title, item.findtext("description", ""), item.findtext("pubDate", "")


def northrop_items(response, feed):
    html = response.read().decode("utf-8")
    match = re.search(r'<script\b[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError("Northrop newsroom is missing its published page data")
    route = json.loads(match[1])["props"]["pageProps"]["layoutData"]["sitecore"]["route"]
    components = route["placeholders"]["news-main"]
    carousel = [item for component in components
                for item in (component.get("fields") or {}).get("CarouselItems", [])]
    if not carousel:
        raise ValueError("No featured stories found in the Northrop newsroom")
    for item in carousel:
        fields = item["fields"]
        yield (
            urljoin(feed, item["url"]), fields["Headline"]["value"],
            fields["Short Description"]["value"], fields["Published Date"]["value"],
        )


def issuer_records(ticker, feed, cutoff, observed):
    parser = northrop_items if ticker == "NOC" else rss_items
    items, _ = fetch(feed, lambda response: list(parser(response, feed)), user_agent=AGENT)
    records = []
    collected = datetime.now(UTC)
    for link, title, description, date in items:
        title = short_text(title, 200)
        if NEWS_SKIP.search(title):
            continue
        kind = next((kind for kind, pattern in NEWS_TYPES.items() if re.search(pattern, title, re.I)), None)
        if not kind or not date:
            continue
        published = parse_time(date)
        if not cutoff <= published <= observed:
            continue
        target = urlsplit(link)
        if target.scheme != "https" or target.hostname != urlsplit(feed).hostname or not target.path.strip("/"):
            raise ValueError(f"Unexpected issuer article link: {link}")
        link = target._replace(query="", fragment="").geturl()
        records.append(evidence(
            ticker, "issuer_news", link, link, kind, published, collected,
            headline=title, brief=short_text(description, 320),
        ))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", action="append", choices=WATCHLIST, help="Repeat to select issuers")
    parser.add_argument("--source", choices=("all", "sec", "issuer"), default="all")
    parser.add_argument("--days", type=int, default=60, help="Publication lookback in days")
    parser.add_argument("--sec-contact", help="Operator contact to include in the SEC User-Agent")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be positive")
    observed = datetime.now(UTC)
    cutoff = observed - timedelta(days=args.days)
    agent = f"{AGENT} {args.sec_contact}" if args.sec_contact else AGENT
    failed = False
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        with output_lock(args.output_dir):
            path = args.output_dir / "company_summaries.txt"
            for ticker in dict.fromkeys(args.ticker or WATCHLIST):
                if args.source in ("all", "sec"):
                    try:
                        records, warning = sec_records(ticker, cutoff, observed, agent)
                        added, _ = merge_records(path, records)
                        print(f"{ticker} SEC: {len(records)} summaries, {added} new")
                        failed |= warning
                    except SOURCE_ERRORS as error:
                        print(f"{ticker} SEC: {error}", file=sys.stderr)
                        failed = True
                if args.source in ("all", "issuer"):
                    for feed in WATCHLIST[ticker][2]:
                        try:
                            records = issuer_records(ticker, feed, cutoff, observed)
                            added, _ = merge_records(path, records)
                            print(f"{ticker} issuer: {len(records)} summaries, {added} new ({feed})")
                        except SOURCE_ERRORS as error:
                            print(f"{ticker} issuer ({feed}): {error}", file=sys.stderr)
                            failed = True
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, f"Company collection failed: {error}\n")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
