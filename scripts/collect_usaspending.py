"""Collect USAspending prime contract transactions into daily JSON Lines text files."""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_transaction/"
MIN_DATE = date(2007, 10, 1)
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "var" / "usaspending"
PAGE_SIZE = 100
MAX_PAGES = 500
FIELDS = (
    "Award ID", "Mod", "Action Date", "Action Type", "Award Type", "Transaction Amount",
    "Transaction Description", "Recipient Name", "Recipient UEI", "Awarding Agency",
    "Awarding Sub Agency", "Funding Agency", "Funding Sub Agency", "naics_code",
    "product_or_service_code", "generated_internal_id",
)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def retry_delay(attempt, retry_after=None):
    if retry_after:
        try:
            return max(0, int(retry_after))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return 2 ** attempt


def fetch_page(day, page):
    request = Request(
        API_URL,
        data=json_text({
            "filters": {
                "award_type_codes": ["A", "B", "C", "D"],
                "time_period": [{"start_date": day, "end_date": day}],
            },
            "fields": FIELDS,
            "limit": PAGE_SIZE,
            "page": page,
            "sort": "generated_internal_id",
            "order": "asc",
        }).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "QuantLab USAspending collector"},
    )
    for attempt in range(5):
        try:
            with urlopen(request, timeout=45) as response:
                return json.load(response)
        except HTTPError as error:
            with error:
                if attempt == 4 or error.code not in (408, 429, 500, 502, 503, 504):
                    detail = error.read(300).decode("utf-8", errors="replace")
                    raise RuntimeError(f"{day} page {page}: HTTP {error.code} {detail}") from error
                delay = retry_delay(attempt, error.headers.get("Retry-After"))
        except (URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            if attempt == 4:
                raise
            delay = retry_delay(attempt)
        time.sleep(delay)


def transactions(day):
    for page in range(1, MAX_PAGES + 1):
        result = fetch_page(day, page)
        observed_at = datetime.now(UTC).isoformat(timespec="seconds")
        metadata = result["page_metadata"]
        rows = result["results"]
        has_next = metadata["hasNext"]
        if metadata["page"] != page or not isinstance(has_next, bool):
            raise RuntimeError(f"{day}: invalid pagination metadata for page {page}")
        if has_next and len(rows) != PAGE_SIZE:
            raise RuntimeError(f"{day}: incomplete page {page}")
        for row in rows:
            if row["Action Date"] != day or not row["generated_internal_id"]:
                raise RuntimeError(f"{day}: unexpected transaction on page {page}")
            # The API adds an internal database award ID. The generated award ID and
            # modification number identify the contract action independently of that ID.
            source_record = {field: row[field] for field in FIELDS}
            award_id = source_record["generated_internal_id"]
            yield {
                "transaction_key": [award_id, source_record["Mod"]],
                "version_sha256": hashlib.sha256(json_text(source_record).encode("utf-8")).hexdigest(),
                "action_date": day,
                "published_at_utc": None,
                "first_observed_at_utc": observed_at,
                "source_url": f"https://www.usaspending.gov/award/{quote(award_id, safe='')}/",
                "source_api_url": API_URL,
                "source_record": source_record,
            }
        if not has_next and (page < MAX_PAGES or len(rows) < PAGE_SIZE):
            return
        time.sleep(0.2)
    raise RuntimeError(f"{day}: search result limit reached. Use a USAspending bulk download.")


@contextmanager
def output_lock(directory):
    """Prevent concurrent writer processes from replacing each other's output."""
    with (directory / ".collector.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f"Could not lock {directory}. Check for another collector.") from error
        yield


def collect_day(path, day):
    return merge_records(path, transactions(day))


def merge_records(path, records):
    existing = {}
    if path.exists():
        with path.open(encoding="utf-8") as file:
            for line in file:
                record = json.loads(line)
                existing[record["version_sha256"]] = record
    before = len(existing)
    for record in records:
        existing.setdefault(record["version_sha256"], record)
    added = len(existing) - before
    if added or not path.exists():
        write_json_lines(path, existing.values())
    return added, len(existing)


def write_json_lines(path, records):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False,
        ) as file:
            temporary = Path(file.name)
            for record in records:
                file.write(json_text(record) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="First action date")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="Last action date")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not MIN_DATE <= args.start <= args.end:
        parser.error(f"Require {MIN_DATE} <= --start <= --end")
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        with output_lock(args.output_dir):
            for offset in range((args.end - args.start).days + 1):
                day = (args.start + timedelta(days=offset)).isoformat()
                added, total = collect_day(args.output_dir / f"{day}.txt", day)
                print(f"{day}  {added} new versions, {total} saved", flush=True)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        print(f"USAspending collection failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
