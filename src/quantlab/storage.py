import csv
import gzip
import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .domain import Bar, InsiderEvent, instant

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "var"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Store:
    def __init__(self, root=STATE):
        self.root = Path(root)
        (self.root / "market").mkdir(parents=True, exist_ok=True)
        (self.root / "runs").mkdir(exist_ok=True)
        self.db = self.root / "research.sqlite"
        with self.connect() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT);
            INSERT OR IGNORE INTO schema_versions VALUES(1, CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS insider_events(event_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, created TEXT, status TEXT, spec TEXT,
                progress REAL DEFAULT 0, message TEXT DEFAULT '', metrics TEXT, batch_id TEXT);
            CREATE TABLE IF NOT EXISTS stock_batches(
                id TEXT PRIMARY KEY, created TEXT NOT NULL, report TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def datasets(self):
        with self.connect() as c:
            return [
                json.loads(r[0])
                for r in c.execute("SELECT metadata FROM datasets ORDER BY rowid DESC")
            ]

    def overview(self):
        tables = ("datasets", "insider_events", "runs", "holdouts", "replay")
        with self.connect() as c:
            present = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counts = {
                t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in tables
                if t in present
            }
            statuses = dict(c.execute("SELECT status,COUNT(*) FROM runs GROUP BY status"))
        return {
            "path": str(self.db),
            "counts": counts,
            "statuses": statuses,
            "database_bytes": self.db.stat().st_size,
            "market_files": len(list((self.root / "market").glob("*.csv.gz"))),
            "result_files": len(list((self.root / "runs").glob("*/result.json"))),
            "bars": sum(d["bars"] for d in self.datasets()),
        }

    def dataset(self, dataset_id):
        with self.connect() as c:
            row = c.execute("SELECT metadata FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        if not row:
            raise ValueError("Dataset not found")
        return json.loads(row[0])

    def save_bars(self, bars, metadata):
        if not bars:
            raise ValueError("No usable bars returned")
        bars = sorted(bars, key=lambda b: (b.symbol, b.time))
        seen, quality = set(), []
        for b in bars:
            instant(b.time)
            instant(b.session_end)
            from datetime import date

            date.fromisoformat(b.session)
            if instant(b.time) >= instant(b.session_end):
                raise ValueError("Bar start must precede session close")
            if not all(math.isfinite(v) for v in (b.open, b.high, b.low, b.close, b.volume)):
                raise ValueError("OHLC and volume must be finite")
            if (
                not (0 < b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high)
                or b.volume < 0
            ):
                raise ValueError(f"Invalid OHLC/volume: {b.symbol} {b.time}")
            if (b.symbol, b.time) in seen:
                raise ValueError("Duplicate symbol/timestamp")
            seen.add((b.symbol, b.time))
        identity = digest({"bars": [asdict(b) for b in bars], "provenance": metadata})
        dataset_id = identity[:16]
        with gzip.open(
            self.root / "market" / f"{dataset_id}.csv.gz", "wt", newline="", encoding="utf8"
        ) as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(bars[0])))
            writer.writeheader()
            writer.writerows(asdict(b) for b in bars)
        quality.extend(metadata.get("quality", []))
        info = {
            **metadata,
            "id": dataset_id,
            "hash": identity,
            "created": now(),
            "bars": len(bars),
            "symbols": sorted({b.symbol for b in bars}),
            "start": min(b.session for b in bars),
            "end": max(b.session for b in bars),
            "quality": quality,
        }
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO datasets VALUES(?,?)", (dataset_id, json.dumps(info)))
        return info

    def load_bars(self, dataset_id):
        self.dataset(dataset_id)  # only registered identifiers can become paths
        result = {}
        with gzip.open(self.root / "market" / f"{dataset_id}.csv.gz", "rt", encoding="utf8") as f:
            for row in csv.DictReader(f):
                for key in ("open", "high", "low", "close", "volume"):
                    row[key] = float(row[key])
                b = Bar(**row)
                result.setdefault(b.symbol, []).append(b)
        return result

    def put_events(self, events):
        with self.connect() as c:
            before = c.total_changes
            c.executemany(
                "INSERT OR IGNORE INTO insider_events VALUES(?,?)",
                [(e.event_id, json.dumps(asdict(e))) for e in events],
            )
            return c.total_changes - before

    def events(self):
        with self.connect() as c:
            return [
                InsiderEvent(**json.loads(r[0]))
                for r in c.execute("SELECT payload FROM insider_events")
            ]

    def create_run(self, spec, batch_id=None):
        run_id = uuid.uuid4().hex[:16]
        with self.connect() as c:
            c.execute(
                "INSERT INTO runs(id,created,status,spec,batch_id) VALUES(?,?,?,?,?)",
                (run_id, now(), "queued", json.dumps(spec), batch_id),
            )
        return run_id

    def update_run(self, run_id, status, progress=0, message="", metrics=None):
        with self.connect() as c:
            c.execute(
                "UPDATE runs SET status=?,progress=?,message=?,metrics=? WHERE id=?",
                (
                    status,
                    progress,
                    message,
                    json.dumps(metrics) if metrics is not None else None,
                    run_id,
                ),
            )

    def runs(self):
        with self.connect() as c:
            return [
                {
                    **dict(r),
                    "spec": json.loads(r["spec"]),
                    "metrics": json.loads(r["metrics"]) if r["metrics"] else None,
                }
                for r in c.execute("SELECT * FROM runs ORDER BY created DESC LIMIT 500")
            ]

    def stock_backtest_history(self):
        with self.connect() as c:
            rows = c.execute("""
                SELECT r.id, r.created, r.spec, r.metrics,
                       json_extract(d.metadata, '$.feed') AS feed
                FROM runs r
                LEFT JOIN datasets d ON d.id = json_extract(r.spec, '$.dataset')
                WHERE r.status = 'complete' AND json_extract(r.spec, '$.split') = 'recent'
                ORDER BY r.created DESC, r.id
                """)
            history = []
            for row in rows:
                spec, metrics = json.loads(row["spec"]), json.loads(row["metrics"])
                rate, trades = metrics["win_rate"], metrics["trades"]
                history.append(
                    {
                        "run_id": row["id"],
                        "created": row["created"],
                        "symbol": spec["symbols"][0],
                        "strategy": spec["strategy"],
                        "start": spec["start"],
                        "end": spec["end"],
                        "feed": row["feed"],
                        "net_pnl": round(metrics["end_equity"] - spec["risk"]["capital"], 2) or 0,
                        "trades": trades,
                        "wins": round(rate * trades) if rate is not None else 0,
                        "win_rate": rate,
                        "drawdown": metrics["max_drawdown"],
                    }
                )
            return history

    def save_stock_batch(self, batch_id, report):
        with self.connect() as c:
            c.execute(
                "INSERT INTO stock_batches VALUES(?,?,?)",
                (batch_id, now(), json.dumps(report, allow_nan=False)),
            )

    def stock_batches(self):
        with self.connect() as c:
            return [
                dict(row)
                for row in c.execute("""
                    SELECT id, created, json_extract(report, '$.mode') AS mode,
                           json_extract(report, '$.feed') AS feed,
                           json_extract(report, '$.period') AS period,
                           json_array_length(report, '$.symbols') AS symbols,
                           json_array_length(report, '$.strategies') AS strategies
                    FROM stock_batches ORDER BY created DESC, id
                    """)
            ]

    def stock_batch(self, batch_id):
        with self.connect() as c:
            row = c.execute("SELECT report FROM stock_batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            raise ValueError("Saved stock backtest batch not found")
        return json.loads(row["report"])

    def run(self, run_id):
        with self.connect() as c:
            r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            raise ValueError("Run not found")
        return {
            **dict(r),
            "spec": json.loads(r["spec"]),
            "metrics": json.loads(r["metrics"]) if r["metrics"] else None,
        }

    def save_result(self, run_id, result):
        self.run(run_id)
        path = self.root / "runs" / run_id
        path.mkdir(exist_ok=True)
        (path / "result.json").write_text(json.dumps(result, allow_nan=False), encoding="utf8")
        (path / "manifest.json").write_text(
            json.dumps(result["manifest"], indent=2), encoding="utf8"
        )
        if result["trades"]:
            with (path / "trades.csv").open("w", newline="", encoding="utf8") as f:
                w = csv.DictWriter(f, fieldnames=list(result["trades"][0]))
                w.writeheader()
                w.writerows(result["trades"])

    def result(self, run_id):
        self.run(run_id)
        path = self.root / "runs" / run_id / "result.json"
        if not path.exists():
            raise ValueError("Run has no result yet")
        return json.loads(path.read_text(encoding="utf8"))
