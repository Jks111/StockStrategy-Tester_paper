import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_research import bar, dataset, simulate, temporary

from quantlab.data.audit import audit
from quantlab.jobs import Service
from quantlab.markets import market_hours
from quantlab.storage import Store


class AdditionsTests(unittest.TestCase):
    def test_whole_shares_reject_unaffordable_stock(self):
        bars = [bar("2026-01-05", 600, 600), bar("2026-01-06", 600, 600)]
        whole = simulate(bars, risk={"capital": 1000, "share_mode": "whole"})
        fractional = simulate(bars, risk={"capital": 1000, "share_mode": "fractional"})
        self.assertEqual(whole["trades"], [])
        self.assertEqual(whole["metrics"]["end_equity"], 1000)
        self.assertEqual(whole["metrics"]["rejected_entries"], 1)
        self.assertGreater(fractional["trades"][0]["quantity"], 0)
        self.assertLess(fractional["trades"][0]["quantity"], 1)

    def test_whole_shares_are_rounded_down(self):
        result = simulate(
            [bar("2026-01-05", 90, 90), bar("2026-01-06", 90, 90)],
            risk={"capital": 1000, "share_mode": "whole"},
        )
        self.assertEqual(result["trades"][0]["quantity"], 2)

    def test_us_holiday_and_early_close(self):
        holiday = market_hours(datetime.fromisoformat("2026-11-26T16:00:00+00:00"))["markets"][1]
        self.assertEqual(holiday["today"], "No session today")
        early = market_hours(datetime.fromisoformat("2026-11-27T16:00:00+00:00"))["markets"][1]
        self.assertTrue(early["early_close"])
        self.assertEqual(early["today"], "16:30–20:00")
        closed = market_hours(datetime.fromisoformat("2026-11-27T18:00:00+00:00"))["markets"][1]
        self.assertEqual(closed["status"], "Closed for today")

    def test_audit_flags_missing_holiday_and_wrong_close(self):
        bars = [bar("2026-11-25"), bar("2026-11-26"), bar("2026-11-27"), bar("2026-12-01")]
        report = audit(dataset(bars), {"SPY": bars})
        row = report["symbols"][0]
        self.assertEqual(row["unexpected_sessions"], 1)
        self.assertEqual(row["missing_sessions"], 1)
        self.assertEqual(row["boundary_errors"], 1)
        self.assertEqual(report["status"], "review required")

    def test_combined_budget_and_overlap_are_saved(self):
        with temporary() as root:
            store = Store(root)
            bars = [bar(f"2026-01-{d:02d}") for d in (5, 6, 7, 8, 9)]
            meta = store.save_bars(bars, {"source": "csv", "timeframe": "1Day"})
            service = Service(store)
            child = {
                "strategy": "buy_hold",
                "dataset": meta["id"],
                "risk": {"spread_bps": 0, "slippage_bps": 0, "position_fraction": 1},
            }
            run_id = service.submit_portfolio(
                {
                    "capital": 1000,
                    "components": [{"spec": child, "weight": 0.5}, {"spec": child, "weight": 0.5}],
                }
            )
            service.pool.shutdown(wait=True)
            self.assertEqual(store.run(run_id)["status"], "complete", store.run(run_id)["message"])
            result = store.result(run_id)
            self.assertEqual(result["metrics"]["end_equity"], 1000)
            self.assertEqual(sum(c["spec"]["risk"]["capital"] for c in result["components"]), 1000)
            self.assertAlmostEqual(result["metrics"]["peak_symbol_exposure"], 1)
            self.assertEqual(len(result["trades"]), 2)
            self.assertEqual(store.overview()["counts"]["runs"], 1)

    def test_combined_rejects_overspending_and_mixed_dates(self):
        with temporary() as root:
            store = Store(root)
            meta = store.save_bars(
                [bar(f"2026-01-{d:02d}") for d in (5, 6, 7, 8, 9)],
                {"source": "csv", "timeframe": "1Day"},
            )
            service = Service(store)
            child = {"strategy": "buy_hold", "dataset": meta["id"]}
            try:
                with self.assertRaisesRegex(ValueError, "100%"):
                    service.submit_portfolio({"components": [{"spec": child, "weight": 0.7}] * 2})
                with self.assertRaisesRegex(ValueError, "same dataset and date"):
                    service.submit_portfolio(
                        {
                            "components": [
                                {"spec": child, "weight": 0.5},
                                {"spec": {**child, "start": "2026-01-06"}, "weight": 0.5},
                            ]
                        }
                    )
                self.assertEqual(store.overview()["counts"]["runs"], 0)
            finally:
                service.pool.shutdown(wait=True)
