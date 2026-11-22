"""Collect official Fed releases and GDELT event metadata into text files."""

import argparse
import csv
import hashlib
import io
import re
import sys
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from collect_contract_announcements import fetch, record, rss_records, utc
from collect_usaspending import merge_records, output_lock

FED = "https://www.federalreserve.gov/feeds/press_monetary.xml"
GDELT = "https://data.gdeltproject.org/gdeltv2/"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "var" / "market_news"


def gdelt_records():
    index, _ = fetch(GDELT + "lastupdate.txt", lambda response: response.read().decode("ascii"))
    for line in index.splitlines():
        size, checksum, url = line.split()
        name = urlsplit(url).path.rsplit("/", 1)[-1]
        if re.fullmatch(r"\d{14}\.export\.CSV\.zip", name):
            break
    else:
        raise ValueError("GDELT index has no event export")
    batch_url = GDELT + name

    def parse(response):
        archive = response.read()
        if len(archive) != int(size) or hashlib.md5(
            archive, usedforsecurity=False,
        ).hexdigest() != checksum:
            raise ConnectionError("GDELT archive does not match the index")
        observed = utc(datetime.now(UTC))
        records = []
        with ZipFile(io.BytesIO(archive)) as zipped:
            with zipped.open(name.removesuffix(".zip")) as member:
                rows = csv.reader(io.TextIOWrapper(member, encoding="utf-8"), delimiter="\t")
                for row in rows:
                    if len(row) != 61:
                        raise ValueError("Unexpected GDELT event schema")
                    event_id, event_date = row[:2]
                    source_url = row[60]
                    payload = {
                        "actor_1": row[6], "actor_2": row[16], "event_code": row[26],
                        "event_date": event_date, "source_url": source_url,
                    }
                    records.append(record(
                        "gdelt", event_id, source_url, payload, feed_url=batch_url,
                        event_date=datetime.strptime(event_date, "%Y%m%d").date().isoformat(),
                        indexed_at_utc=utc(datetime.strptime(row[59], "%Y%m%d%H%M%S").replace(
                            tzinfo=UTC,
                        )),
                        published_at_utc=None, first_observed_at_utc=observed,
                    ))
        return records

    records, _ = fetch(batch_url, parse)
    return records


def fed_records():
    records, _ = fetch(FED, lambda response: rss_records(response, "fed", FED))
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("all", "fed", "gdelt"), default="all")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    sources = {"fed": fed_records, "gdelt": gdelt_records}
    failed = False
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        with output_lock(args.output_dir):
            for source, collect in sources.items():
                if args.source not in ("all", source):
                    continue
                try:
                    added, total = merge_records(args.output_dir / f"{source}.txt", collect())
                    print(f"{source}: {added} new versions, {total} saved")
                except (OSError, ValueError, HTTPException, BadZipFile, ElementTree.ParseError) as error:
                    print(f"{source}: {error}", file=sys.stderr)
                    failed = True
    except (OSError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
