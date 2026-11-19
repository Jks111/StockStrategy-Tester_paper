import csv
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..domain import Bar, InsiderEvent, instant
from ..storage import ROOT, digest, now

NY = ZoneInfo("America/New_York")


def credentials():
    values = dict(os.environ)
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return values


def request_json(url, headers=None):
    # Broker requests have fixed HTTPS endpoints. Never forward their keys on redirects.
    authenticated = any(
        k.lower() in {"authorization", "apca-api-key-id", "apca-api-secret-key"}
        for k in (headers or {})
    )
    open_url = (
        urllib.request.build_opener(_RejectRedirects()).open
        if authenticated
        else urllib.request.urlopen
    )
    for attempt in range(4):
        try:
            with open_url(urllib.request.Request(url, headers=headers or {}), timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 3:
                time.sleep(min(15, 2 ** (attempt + 1)))
                continue
            raise ValueError(
                f"Provider returned HTTP {error.code}; check credentials, feed entitlement and request dates"
            ) from None
        except urllib.error.URLError:
            raise ValueError("Provider connection failed; check network access") from None
    raise ValueError("Provider rate limit exceeded")


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Alpaca:
    def __init__(self):
        env = credentials()
        key, secret = env.get("ALPACA_API_KEY"), env.get("ALPACA_API_SECRET")
        if not key or not secret:
            raise ValueError("Add ALPACA_API_KEY and ALPACA_API_SECRET to the local .env file")
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def history(self, symbols, start, end, timeframe="1Day", feed="sip", *, cutoff=None):
        if timeframe not in ("1Day", "5Min", "1Min") or feed not in ("sip", "iex"):
            raise ValueError("Unsupported timeframe/feed")
        completed_only = cutoff is not None
        if cutoff is None:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=16)
        end_dt = min(
            datetime.fromisoformat(end).replace(tzinfo=NY if completed_only else timezone.utc)
            + timedelta(days=1),
            cutoff,
        )
        interval = timedelta(minutes=int(timeframe[:-3])) if timeframe != "1Day" else None
        params = {
            "symbols": ",".join(symbols),
            "start": start,
            "end": end_dt.isoformat(),
            "timeframe": timeframe,
            "feed": feed,
            "adjustment": "split",
            "limit": 10000,
            "sort": "asc",
        }
        # Paper calendar is read-only; use it for holidays and early closes.
        calendar = request_json(
            "https://paper-api.alpaca.markets/v2/calendar?"
            + urllib.parse.urlencode({"start": start, "end": end}),
            self.headers,
        )
        sessions = {s["date"]: s for s in calendar}
        bars, page = [], None
        for _ in range(2000):
            if page:
                params["page_token"] = page
            data = request_json(
                "https://data.alpaca.markets/v2/stocks/bars?" + urllib.parse.urlencode(params),
                self.headers,
            )
            for symbol, rows in data.get("bars", {}).items():
                for row in rows:
                    dt = instant(row["t"]).astimezone(NY)
                    session = dt.date().isoformat()
                    if session not in sessions:
                        continue
                    entry = sessions[session]
                    close_time = datetime.fromisoformat(session + "T" + entry["close"]).replace(
                        tzinfo=NY
                    )
                    open_time = datetime.fromisoformat(session + "T" + entry["open"]).replace(
                        tzinfo=NY
                    )
                    if timeframe != "1Day" and not open_time <= dt < close_time:
                        continue
                    if completed_only:
                        bar_end = min(dt + interval, close_time) if interval else close_time
                        if bar_end > end_dt:
                            continue
                    timestamp = open_time if timeframe == "1Day" else dt
                    bars.append(
                        Bar(
                            symbol,
                            timestamp.astimezone(timezone.utc).isoformat(),
                            row["o"],
                            row["h"],
                            row["l"],
                            row["c"],
                            row["v"],
                            session,
                            close_time.astimezone(timezone.utc).isoformat(),
                        )
                    )
            page = data.get("next_page_token")
            if not page:
                return bars
            time.sleep(0.35)
        raise ValueError("Download exceeds pagination safety limit; split the date range")

    def quotes(self, symbols):
        return request_json(
            "https://data.alpaca.markets/v2/stocks/quotes/latest?"
            + urllib.parse.urlencode({"symbols": ",".join(symbols), "feed": "iex"}),
            self.headers,
        )


def parse_csv(text):
    bars = []
    for row in csv.DictReader(io.StringIO(text)):
        required = (
            "symbol",
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "session",
            "session_end",
        )
        if not all(k in row for k in required):
            raise ValueError("CSV needs columns: " + ", ".join(required))
        bars.append(
            Bar(
                row["symbol"].strip().upper(),
                instant(row["time"]).isoformat(),
                *[float(row[k]) for k in ("open", "high", "low", "close", "volume")],
                row["session"],
                instant(row["session_end"]).isoformat(),
            )
        )
    return bars


def parse_insider_csv(text):
    result = []
    for row in csv.DictReader(io.StringIO(text)):
        if not all(k in row for k in ("symbol", "insider", "transaction_date", "shares", "price")):
            raise ValueError(
                "Insider CSV needs symbol, insider, transaction_date, shares, price; add available_at for backtests"
            )
        available = instant(row["available_at"]).isoformat() if row.get("available_at") else None
        result.append(
            InsiderEvent(
                row.get("event_id") or digest(row),
                row["symbol"].strip().upper(),
                row["insider"],
                row["transaction_date"],
                available,
                row.get("observed_at") or now(),
                row.get("code", "UNKNOWN"),
                float(row["shares"]),
                float(row["price"]),
                row.get("source", "csv"),
                row.get("role", ""),
                row.get("accession", ""),
                row.get("ownership", ""),
                row.get("amended", "").lower() in ("true", "1"),
            )
        )
    return result


def parse_form4(xml, accession, accepted_at, source="SEC EDGAR"):
    if len(xml) > 5_000_000 or "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ValueError("Invalid or oversized Form 4 XML")
    root = ET.fromstring(xml)

    def value(node, path, default=""):
        return node.findtext(path, default=default)

    symbol = value(root, ".//issuerTradingSymbol").strip().upper()
    insider = value(root, ".//rptOwnerName")
    amended = value(root, ".//documentType") == "4/A"
    result = []
    for i, node in enumerate(root.findall(".//nonDerivativeTransaction")):
        shares = value(node, "transactionAmounts/transactionShares/value", "0")
        price = value(node, "transactionAmounts/transactionPricePerShare/value", "0")
        result.append(
            InsiderEvent(
                f"{accession}:{i}",
                symbol,
                insider,
                value(node, "transactionDate/value"),
                instant(accepted_at).isoformat(),
                now(),
                value(node, "transactionCoding/transactionCode"),
                float(shares or 0),
                float(price or 0),
                source,
                value(root, ".//officerTitle"),
                accession,
                value(node, "ownershipNature/directOrIndirectOwnership/value"),
                amended,
            )
        )
    return result


def fetch_form4(cik, limit=10):
    env = credentials()
    agent = env.get("SEC_USER_AGENT", "")
    if "@" not in agent or "example.com" in agent:
        raise ValueError("Set SEC_USER_AGENT to your name and contact email in .env")
    cik = str(int(cik)).zfill(10)
    headers = {"User-Agent": agent, "Accept-Encoding": "identity"}
    recent = request_json(f"https://data.sec.gov/submissions/CIK{cik}.json", headers)["filings"][
        "recent"
    ]
    events = []
    for i, form in enumerate(recent["form"]):
        if form not in ("4", "4/A"):
            continue
        accession = recent["accessionNumber"][i]
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/index.json"
        directory = request_json(url, headers)["directory"]["item"]
        candidates = [x["name"] for x in directory if x["name"].endswith(".xml")]
        for filename in candidates:
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url.rsplit("/", 1)[0] + "/" + filename, headers=headers),
                    timeout=25,
                ) as r:
                    xml = r.read(5_000_001).decode("utf8")
                if "ownershipDocument" not in xml:
                    continue
                accepted = recent["acceptanceDateTime"][i]
                if not (accepted.endswith("Z") or "+" in accepted[10:]):
                    accepted = (
                        datetime.fromisoformat(accepted)
                        .replace(tzinfo=NY)
                        .astimezone(timezone.utc)
                        .isoformat()
                    )
                events.extend(parse_form4(xml, accession, accepted))
                break
            finally:
                time.sleep(0.25)
        limit -= 1
        if limit <= 0:
            break
    return events


def import_legacy(path):
    import sqlite3

    path = Path(path).resolve()
    if not path.is_file() or path.suffix != ".db":
        raise ValueError("Choose an existing .db file")
    result = []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM simulations"):
            data = dict(row)
            result.append(
                InsiderEvent(
                    "legacy-" + digest([str(path), data]),
                    data.get("symbol", ""),
                    data.get("insider", ""),
                    "unknown",
                    None,
                    now(),
                    "UNKNOWN",
                    data.get("total_insider_shares") or 0,
                    data.get("weighted_avg_price") or 0,
                    "legacy snapshot (not a filing)",
                    snapshot_date=data.get("snapshot_date", "unknown"),
                )
            )
    return result
