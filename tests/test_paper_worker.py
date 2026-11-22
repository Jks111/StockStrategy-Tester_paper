import json
import sqlite3
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_research import temporary

from quantlab.domain import Bar
from quantlab.paper_worker import configure, run_once
from quantlab.storage import Store

START = "2026-11-27"  # Half-day following Thanksgiving.
OPEN = datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc)


def fixture():
    def bar(t, price, end):
        return Bar(
            "AAPL",
            t.isoformat(),
            price,
            price + 1,
            price - 1,
            price,
            100,
            t.date().isoformat(),
            end.isoformat(),
        )

    earlier = OPEN - timedelta(days=2)
    return [
        bar(earlier + timedelta(minutes=5 * i), 100, earlier + timedelta(hours=6, minutes=30))
        for i in range(20)
    ] + [
        bar(OPEN + timedelta(minutes=5 * i), 105 + i, OPEN + timedelta(hours=3, minutes=30))
        for i in range(3)
    ]


def decisions(store):
    with store.connect() as db:
        return [
            tuple(row)
            for row in db.execute(
                "SELECT bar_end,outcome,signal FROM paper_signal_decisions ORDER BY bar_end"
            )
        ]


class PaperWorkerTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(temporary())
        self.store = Store(root)
        self.version = configure(self.store, "aapl", "iex", START)
        self.rows = fixture()

    def observe(self, minutes, rows=None):
        return run_once(
            self.store,
            as_of=OPEN + timedelta(minutes=minutes),
            history=lambda *a, **kw: self.rows if rows is None else rows,
        )

    def test_watchlist_independent_signals_and_resume(self):
        configure(self.store, ["aapl,JPM", "AAPL"], "iex", START)
        rows = self.rows + [replace(b, symbol="JPM") for b in self.rows]
        result = self.observe(10, rows)
        self.assertEqual(set(result["symbols"]), {"AAPL", "JPM"})
        self.assertEqual(result["decisions_added"], 4)
        self.assertEqual(self.observe(10, rows)["decisions_added"], 0)
        configure(self.store, "JPM", "iex", START)
        self.assertEqual(self.observe(15, rows)["decisions_added"], 1)

    def test_completed_bar_and_overlap(self):
        self.assertEqual(self.observe(4)["decisions_added"], 0)
        self.assertEqual(self.observe(5)["decisions_added"], 1)
        self.assertEqual(self.observe(10, self.rows[::-1] + [self.rows[-2]])["decisions_added"], 1)
        self.assertEqual(self.observe(10)["decisions_added"], 0)
        opening, following = decisions(self.store)
        self.assertEqual(opening[:2], ((OPEN + timedelta(minutes=5)).isoformat(), "signal"))
        self.assertEqual(json.loads(opening[2])["reason"], "Opening candle above EMA")
        self.assertEqual(following[1:], ("no_signal", None))

    def test_rollback_and_resume_matches_uninterrupted(self):
        self.observe(5)
        with self.store.connect() as db:
            db.execute("""CREATE TRIGGER crash BEFORE INSERT ON paper_signal_decisions
                          WHEN NEW.bar_end LIKE '%14:45:%' BEGIN
                          SELECT RAISE(ABORT, 'interrupted'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "interrupted"):
            self.observe(15)
        self.assertEqual(len(decisions(self.store)), 1)
        with self.store.connect() as db:
            db.execute("DROP TRIGGER crash")
        self.store = Store(self.store.root)
        self.assertEqual(self.observe(15)["decisions_added"], 2)
        with temporary() as root:
            clean = Store(root)
            configure(clean, "AAPL", "iex", START)
            run_once(clean, as_of=OPEN + timedelta(minutes=15), history=lambda *a, **kw: self.rows)
            self.assertEqual(decisions(clean), decisions(self.store))

    def test_racing_workers_and_late_bar_in_overlap_session(self):
        barrier = Barrier(2)

        def history(*args, **kwargs):
            barrier.wait(timeout=10)
            return self.rows[:-2] + self.rows[-1:]

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    run_once, self.store, as_of=OPEN + timedelta(minutes=15), history=history
                )
                for _ in range(2)
            ]
            self.assertEqual(sorted(f.result()["decisions_added"] for f in futures), [0, 2])
        self.assertEqual(self.observe(15)["decisions_added"], 1)
        self.assertEqual(len(decisions(self.store)), 3)

    def test_versions_sip_cutoff_and_session_close(self):
        self.assertEqual(configure(self.store, "AAPL", "iex", START), self.version)
        self.assertEqual(configure(self.store, "AAPL", "sip", START), self.version + 1)
        with patch("quantlab.paper_worker.Alpaca") as alpaca:
            alpaca.return_value.history.return_value = self.rows
            result = run_once(self.store, as_of=OPEN + timedelta(minutes=21))
            alpaca.return_value.history.assert_called_once_with(
                ["AAPL"], "2026-11-13", START, "5Min", "sip", cutoff=OPEN + timedelta(minutes=5)
            )
        self.assertEqual(result["decisions_added"], 1)
        close = OPEN + timedelta(hours=3, minutes=30)
        short_bar = replace(self.rows[-1], time=(close - timedelta(minutes=2)).isoformat())
        self.assertEqual(self.observe(225, [short_bar])["decisions_added"], 0)
        self.assertEqual(self.observe(226, [short_bar])["last_bar_end"], close.isoformat())
