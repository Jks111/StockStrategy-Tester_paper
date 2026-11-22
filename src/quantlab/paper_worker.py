"""One-shot observation of opening EMA signals from completed Alpaca bars."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from itertools import groupby

from .data.providers import NY, Alpaca
from .domain import Context, History, instant, record
from .recent import BAR_INTERVAL
from .strategies import definition, load


def initialize(store):
    with store.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS paper_signal_configs (
                version INTEGER PRIMARY KEY, strategy TEXT NOT NULL, params TEXT NOT NULL,
                symbol TEXT NOT NULL, feed TEXT NOT NULL, timeframe TEXT NOT NULL,
                start_session TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_watchlist (version INTEGER PRIMARY KEY REFERENCES paper_signal_configs(version));
            CREATE TABLE IF NOT EXISTS paper_signal_decisions (
                config_version INTEGER NOT NULL REFERENCES paper_signal_configs(version),
                session TEXT NOT NULL, bar_end TEXT NOT NULL, outcome TEXT NOT NULL,
                signal TEXT, PRIMARY KEY(config_version, bar_end)
            );
            CREATE INDEX IF NOT EXISTS paper_decisions_by_session
                ON paper_signal_decisions(config_version, session, bar_end);
        """)


def configure(store, symbol, feed, start):
    entries = [symbol] if isinstance(symbol, str) else symbol
    symbols = list(
        dict.fromkeys(
            t.upper() for entry in entries for t in re.split(r"[,\s]+", entry.strip()) if t
        )
    )
    if not 1 <= len(symbols) <= 30 or any(
        not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", t) for t in symbols
    ):
        raise ValueError("Enter 1-30 valid US tickers separated by spaces or commas")
    if feed not in ("iex", "sip"):
        raise ValueError("Choose the IEX or SIP feed")
    start = date.fromisoformat(start).isoformat()
    strategy = definition("opening_ema")
    initialize(store)
    versions = {}
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM paper_watchlist")
        for ticker in symbols:
            values = (
                strategy["id"],
                json.dumps(strategy["defaults"], sort_keys=True),
                ticker,
                feed,
                strategy["timeframe"],
                start,
            )
            row = db.execute(
                """SELECT version FROM paper_signal_configs WHERE strategy=? AND
                             params=? AND symbol=? AND feed=? AND timeframe=? AND start_session=?
                             ORDER BY version DESC LIMIT 1""",
                values,
            ).fetchone()
            version = (
                row[0]
                if row
                else db.execute(
                    """INSERT INTO paper_signal_configs
                (strategy,params,symbol,feed,timeframe,start_session) VALUES(?,?,?,?,?,?)""",
                    values,
                ).lastrowid
            )
            db.execute("INSERT INTO paper_watchlist VALUES(?)", (version,))
            versions[ticker] = version
    return next(iter(versions.values())) if len(versions) == 1 else versions


def run_once(store, *, as_of=None, history=None):
    initialize(store)
    with store.connect() as db:
        configs = db.execute("""SELECT c.* FROM paper_signal_configs c JOIN paper_watchlist w
                             ON c.version=w.version ORDER BY c.symbol""").fetchall()
        if not configs:
            configs = db.execute(
                "SELECT * FROM paper_signal_configs ORDER BY version DESC LIMIT 1"
            ).fetchall()
    if not configs:
        raise ValueError("Create an observation with paper-worker --configure first")
    cutoff = as_of or datetime.now(timezone.utc)
    if len(configs) == 1:
        return _observe(store, configs[0], cutoff, history)

    def observe(config):
        try:
            return _observe(store, config, cutoff, history)
        except ValueError as error:
            return {"error": str(error), "symbol": config["symbol"]}

    with ThreadPoolExecutor(max_workers=min(4, len(configs))) as pool:
        results = dict(zip((c["symbol"] for c in configs), pool.map(observe, configs)))
    return {
        "decisions_added": sum(r.get("decisions_added", 0) for r in results.values()),
        "symbols": results,
    }


def _observe(store, config, as_of, history):
    version = config["version"]
    with store.connect() as db:
        params = json.loads(config["params"])
        latest = db.execute(
            """SELECT session,bar_end FROM paper_signal_decisions WHERE config_version=?
               ORDER BY bar_end DESC LIMIT 1""",
            (version,),
        ).fetchone()
        resume_session = latest["session"] if latest else config["start_session"]
        # Include a full EMA window before the overlap session, even for thinly traded symbols.
        lookback = max(200, 5 * params["ema_length"])
        warmup = db.execute(
            """SELECT session FROM paper_signal_decisions WHERE config_version=? AND session < ?
               ORDER BY session DESC, bar_end DESC LIMIT 1 OFFSET ?""",
            (version, resume_session, lookback - 1),
        ).fetchone()

    cutoff = as_of or datetime.now(timezone.utc)
    if config["feed"] == "sip":
        cutoff -= timedelta(minutes=16)
    last_day = cutoff.astimezone(NY).date().isoformat()
    summary = {
        "config_version": version,
        "decisions_added": 0,
        "last_bar_end": latest["bar_end"] if latest else None,
    }
    if last_day < resume_session:
        return summary
    start = (
        warmup[0]
        if warmup
        else (date.fromisoformat(config["start_session"]) - timedelta(days=14)).isoformat()
    )
    if history is None:
        history = Alpaca().history
    rows = history(
        [config["symbol"]], start, last_day, config["timeframe"], config["feed"], cutoff=cutoff
    )
    bars = []
    ends = []
    for bar in sorted(rows, key=lambda row: instant(row.time)):
        end = min(instant(bar.time) + BAR_INTERVAL, instant(bar.session_end))
        if bar.symbol != config["symbol"] or not start <= bar.session <= last_day or end > cutoff:
            continue
        stamp = end.isoformat()
        if ends and ends[-1] == stamp:
            if bars[-1] != bar:
                raise ValueError(f"Conflicting bars ending at {stamp}")
            continue
        ends.append(stamp)
        bars.append(bar)

    evaluate = load(config["strategy"])
    for session, indices in groupby(range(len(bars)), key=lambda i: bars[i].session):
        if session < resume_session:
            continue
        with store.connect() as db:
            # Check again under the write lock in case another invocation committed this session.
            db.execute("BEGIN IMMEDIATE")
            existing = {
                row[0]
                for row in db.execute(
                    "SELECT bar_end FROM paper_signal_decisions WHERE config_version=? AND session=?",
                    (version, session),
                )
            }
            for i in indices:
                stamp = ends[i]
                if stamp in existing:
                    continue
                ctx = Context(History(bars, i + 1), i, [], params, decision_time=stamp)
                signal = evaluate(ctx)
                db.execute(
                    """INSERT INTO paper_signal_decisions(config_version,session,bar_end,outcome,signal)
                       VALUES(?,?,?,?,?)""",
                    (
                        version,
                        session,
                        stamp,
                        "signal" if signal else "no_signal",
                        json.dumps(record(signal), allow_nan=False) if signal else None,
                    ),
                )
                summary["decisions_added"] += 1
    with store.connect() as db:
        summary["last_bar_end"] = db.execute(
            "SELECT MAX(bar_end) FROM paper_signal_decisions WHERE config_version=?", (version,)
        ).fetchone()[0]
    return summary
