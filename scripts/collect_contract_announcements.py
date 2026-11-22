"""Poll official contract award notices and announcement feeds without credentials."""

import argparse
import csv
import hashlib
import io
import re
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from collect_usaspending import json_text, merge_records, output_lock, retry_delay

SAM = ("https://s3.amazonaws.com/falextracts/Contract%20Opportunities/datagov/"
       "ContractOpportunitiesFullCSV.csv")
DEFENSE = "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=10"
NASA = "https://www.nasa.gov/news-release/feed/"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "var" / "contract_announcements"
SAM_FIELDS = (
    "NoticeId", "Title", "Sol#", "Department/Ind.Agency", "Sub-Tier", "Office",
    "PostedDate", "Type", "AwardNumber", "AwardDate", "Award$", "Awardee", "Link",
    "NaicsCode", "ClassificationCode",
)
NASA_AWARD = re.compile(
    r"\b(?:awards?(?:\s+\w+){0,8}\s+contracts?|selects?\b.+\bfor\b.+\bcontracts?)\b",
    re.IGNORECASE,
)


def utc(value):
    return value.astimezone(UTC).isoformat(timespec="seconds")


def record(source, source_id, url, payload, **timestamps):
    version = json_text([source, source_id, payload])
    return {
        "source": source,
        "source_id": source_id,
        "source_url": url,
        "version_sha256": hashlib.sha256(version.encode("utf-8")).hexdigest(),
        **timestamps,
        "source_record": payload,
    }


def fetch(url, parse, etag=None, *, user_agent="QuantLab contract announcement collector", payload=None):
    headers = {"User-Agent": user_agent}
    if etag:
        headers["If-None-Match"] = etag
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json_text(payload).encode("utf-8")
    request = Request(url, data=body, headers=headers)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=60) as response:
                return parse(response), response.headers.get("ETag")
        except HTTPError as error:
            with error:
                if error.code == 304 and etag:
                    return None, etag
                if attempt == 2 or error.code not in (408, 429, 500, 502, 503, 504):
                    raise
                delay = retry_delay(attempt, error.headers.get("Retry-After"))
        except (URLError, TimeoutError, ConnectionError, HTTPException):
            if attempt == 2:
                raise
            delay = retry_delay(attempt)
        time.sleep(delay)


def sam_records(response):
    observed = utc(datetime.now(UTC))
    snapshot = parsedate_to_datetime(response.headers["Last-Modified"])
    # Retain recent postings from the daily snapshot and overlap consecutive runs.
    cutoff = (snapshot.date() - timedelta(days=14)).isoformat()
    records = []
    with tempfile.TemporaryFile() as download:
        shutil.copyfileobj(response, download, length=1024 * 1024)
        length = response.headers.get("Content-Length")
        if length is not None and download.tell() != int(length):
            raise ConnectionError("Incomplete SAM.gov snapshot")
        download.seek(0)
        # The extract can mix UTF-8 with Windows-1252 bytes in notice text.
        with io.TextIOWrapper(
            download, encoding="utf-8-sig", errors="surrogateescape", newline="",
        ) as text:
            csv.field_size_limit(16 * 1024 * 1024)
            reader = csv.DictReader(text, strict=True)
            if not set(SAM_FIELDS).issubset(reader.fieldnames or ()):
                raise ValueError("SAM.gov CSV is missing required fields")
            for row in reader:
                if row["Type"] != "Award Notice" or row["PostedDate"][:10] < cutoff:
                    continue
                notice_id = row["NoticeId"]
                if not notice_id:
                    raise ValueError("SAM.gov award notice is missing its notice ID")
                payload = {
                    name: re.sub(
                        r"[\udc80-\udcff]",
                        lambda match: match[0].encode("utf-8", "surrogateescape").decode(
                            "cp1252", errors="replace",
                        ),
                        row[name],
                    )
                    for name in SAM_FIELDS
                }
                records.append(record(
                    "sam", notice_id, f"https://sam.gov/opp/{notice_id}/view", payload,
                    feed_url=SAM, award_date=row["AwardDate"] or None,
                    posted_at_source=row["PostedDate"], published_at_utc=None,
                    snapshot_modified_at_utc=utc(snapshot), first_observed_at_utc=observed,
                ))
            if reader.line_num <= 1:
                raise ValueError("SAM.gov snapshot has no data rows")
    return records


def rss_records(response, source, feed_url):
    observed = utc(datetime.now(UTC))
    channel = ElementTree.parse(response).getroot().find("channel")
    if channel is None:
        raise ValueError("Expected an RSS channel")
    records = []
    for item in channel.findall("item"):
        title = item.findtext("title", "").strip()
        if source == "nasa" and not NASA_AWARD.search(title):
            continue
        link = item.findtext("link", "").strip()
        if urlsplit(link).scheme != "https" or not urlsplit(link).hostname:
            raise ValueError(f"Invalid announcement URL for {title}")
        published = item.findtext("pubDate", "").strip()
        payload = {
            "title": title, "link": link, "pubDate": published,
            "description": item.findtext("description", "").strip(),
        }
        records.append(record(
            source, item.findtext("guid", "").strip() or link, link, payload,
            feed_url=feed_url, award_date=None,
            published_at_utc=utc(parsedate_to_datetime(published)) if published else None,
            first_observed_at_utc=observed,
        ))
    return records


def collect(source, directory):
    path = directory / f"{source}.txt"
    if source == "sam":
        validator = directory / ".sam-etag"
        etag = validator.read_text(encoding="ascii").strip() if (
            path.exists() and validator.exists()
        ) else None
        records, next_etag = fetch(SAM, sam_records, etag)
        if records is None:
            print("sam: daily extract unchanged")
            return
        added, total = merge_records(path, records)
        validator.write_text(next_etag or "", encoding="ascii")
    else:
        url = {"defense": DEFENSE, "nasa": NASA}[source]
        records, _ = fetch(url, lambda response: rss_records(response, source, url))
        added, total = merge_records(path, records)
    print(f"{source}: {len(records)} matching items, {added} new versions, {total} saved")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("all", "sam", "defense", "nasa"), default="all")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    sources = ("defense", "nasa", "sam") if args.source == "all" else (args.source,)
    failed = False
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        with output_lock(args.output_dir):
            for source in sources:
                try:
                    collect(source, args.output_dir)
                except (
                    OSError, ValueError, RuntimeError, HTTPException, csv.Error,
                    ElementTree.ParseError,
                ) as error:
                    print(f"{source}: {error}", file=sys.stderr)
                    failed = True
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
