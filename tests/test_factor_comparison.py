import math
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quantlab.domain import Bar
from quantlab.factor_comparison import compare


class PairedExitTests(unittest.TestCase):
    def test_same_entry_and_next_bar_exit_with_diverging_rules(self):
        start = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
        calendar, daily = {}, []

        def make(symbol, t, price, close):
            return Bar(
                symbol,
                t.isoformat(),
                price,
                max(price, close),
                min(price, close),
                close,
                100,
                t.date().isoformat(),
                (t + timedelta(hours=6)).isoformat(),
            )

        for i in range(62):
            t = start + timedelta(days=i)
            day = t.date().isoformat()
            calendar[day] = (t, t + timedelta(hours=6))
            for symbol, offset in [("SPY", 1), ("XLK", 2), ("AAPL", 3)]:
                close = 100 + math.sin(i * offset * 0.8)
                daily.append(make(symbol, t, close, close))
        bars = []
        for minutes, price, close in [(0, 100, 103), (5, 102, 102)]:
            for symbol in ["SPY", "XLK", "AAPL"]:
                bars.append(
                    make(
                        symbol,
                        t + timedelta(minutes=minutes),
                        price if symbol == "AAPL" else 100,
                        close if symbol == "AAPL" else 100,
                    )
                )
        report = compare(daily, bars, calendar, [day])
        self.assertEqual(report["paired_entries"], 1)
        for row in report["rows"]:
            self.assertEqual(len(row["trades"]), 1)
            self.assertEqual(row["trades"][0]["exit"], (t + timedelta(minutes=5)).isoformat())
            self.assertEqual(row["open"], [])
            self.assertGreater(row["fees"], 0)
        self.assertEqual(report["rows"][0]["net"], report["rows"][1]["net"])
        # Today's daily close cannot influence either decision.
        self.assertEqual(
            report, compare([b for b in daily if b.session != day], bars, calendar, [day])
        )
