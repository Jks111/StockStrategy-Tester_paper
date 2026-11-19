import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quantlab.markets import market_hours


class MarketHoursTests(unittest.TestCase):
    def test_us_europe_dst_mismatch(self):
        for day, expected in [
            ("2026-01-12", "16:30–23:00"),
            ("2026-03-16", "15:30–22:00"),
            ("2026-07-13", "16:30–23:00"),
            ("2026-10-28", "15:30–22:00"),
        ]:
            with self.subTest(day=day):
                rows = market_hours(datetime.fromisoformat(day + "T12:00:00+00:00"))["markets"]
                self.assertEqual(rows[1]["helsinki"], expected)
                for index in (0, 2, 3):
                    self.assertEqual(rows[index]["helsinki"], "10:00–18:30")

    def test_conversion_date_uses_helsinki_midnight(self):
        self.assertEqual(
            market_hours(datetime.fromisoformat("2026-07-13T22:00:00+00:00"))["date"], "2026-07-14"
        )

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            market_hours(datetime(2026, 1, 12))
