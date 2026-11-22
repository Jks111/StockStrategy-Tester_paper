import sys
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from quantlab.domain import Bar
from quantlab.engine import backtest
from quantlab.recent import incompatibility, selection
from quantlab.spy_open_close import FEE, forecast, random_timing, samples, simulate


def bar(day, index):
    price = 100 + index % 9
    close = price + index % 5 - 2
    return Bar(
        "SPY",
        day + "T14:30:00+00:00",
        price,
        max(price, close),
        min(price, close),
        close,
        500 + index,
        day,
        day + "T21:00:00+00:00",
    )


class SpyTimingTests(unittest.TestCase):
    def test_stock_picker_models_use_pre_window_features_and_exit_at_close(self):
        ids = ["spy_open_close_linear", "spy_open_close_forest"]
        _, strategies, period, _ = selection(
            {"symbols": ["SPY"], "strategies": ids, "period": "1w", "feed": "sip"}
        )
        bars = [bar((date(2024, 1, 1) + timedelta(days=i)).isoformat(), i) for i in range(22)]
        meta = {"id": "test", "source": "csv", "symbols": ["SPY"], "timeframe": "1Day"}
        for strategy in strategies:
            self.assertIsNone(incompatibility("SPY", strategy, period))
            self.assertTrue(incompatibility("AAPL", strategy, period))
            spec = {"strategy": strategy["id"], "start": bars[-1].session, "end": bars[-1].session}
            with patch("quantlab.strategies.spy_timing.predict", return_value=0.01) as predict:
                result = backtest(spec, meta, {"SPY": bars}, [])
            self.assertEqual(predict.call_args.args[0], samples(bars)[0].features)
            (trade,) = result["trades"]
            self.assertEqual(
                (trade["signal_time"], trade["entry_time"], trade["exit_time"]),
                (bars[-2].session_end, bars[-1].time, bars[-1].session_end),
            )
            self.assertEqual(result["spec"]["risk"]["commission"], FEE)
            self.assertFalse(result["open_positions"])

    def test_feature_window_ends_before_entry_and_target_is_intraday(self):
        bars = [bar((date(2019, 1, 1) + timedelta(days=i)).isoformat(), i) for i in range(24)]
        before = samples(bars)
        bars[21] = replace(bars[21], open=95, close=90, volume=10)
        after = samples(bars)
        self.assertEqual(before[0].features, after[0].features)
        self.assertEqual(after[0].feature_session, bars[20].session)
        self.assertAlmostEqual(after[0].target, 90 / 95 - 1)
        self.assertNotEqual(before[1].features, after[1].features)

    def test_holdout_labels_do_not_change_frozen_forecasts(self):
        bars = [
            bar(f"{year}-01-{day:02d}", (year - 2017) * 28 + day)
            for year in range(2017, 2026)
            for day in range(1, 29)
        ]
        rows = samples(bars)
        folds, original = forecast(rows)
        self.assertTrue(all(f["train_end"] < f["start"] for f in folds))
        # Keep observable features fixed and corrupt only the outcomes in the final two years.
        corrupted = [
            replace(r, bar=replace(r.bar, close=1)) if r.bar.session >= "2024" else r for r in rows
        ]
        _, changed = forecast(corrupted)
        self.assertEqual(
            [(r["linear"], r["forest"]) for r in original],
            [(r["linear"], r["forest"]) for r in changed],
        )
        self.assertEqual(
            {r["train_end"] for r in original if r["session"] >= "2024"}, {"2023-01-28"}
        )

    def test_same_fills_fractional_budget_fees_and_early_close(self):
        day = {
            "session": "2024-11-29",
            "open_time": "2024-11-29T14:30:00+00:00",
            "close_time": "2024-11-29T18:00:00+00:00",
            "open": 100,
            "close": 101,
            "linear": 0.01,
            "forest": 0,
        }
        baseline, _, _, trades = simulate([day], "every_open")
        self.assertEqual(simulate([day], "linear")[0], baseline)
        self.assertEqual(trades[0]["quantity"], 1.9992)
        expected = 1.9992 * (101 * 0.9996 - 100 * 1.0004) - 2 * FEE
        self.assertAlmostEqual(baseline["net_pnl"], expected)
        self.assertAlmostEqual(baseline["costs"] + expected, 1.9992)
        self.assertEqual(baseline["average_hold_hours"], 3.5)
        skipped = simulate([day], "forest")[0]
        self.assertEqual(
            (skipped["trades"], skipped["net_pnl"], skipped["open_positions"]), (0, 0.0, 0)
        )
        # A gross forecast can be positive but too small to cover modeled fees.
        self.assertFalse(simulate([{**day, "linear": 0.00085}], "linear")[1][0])
        self.assertEqual(
            random_timing([day], [True], expected)["median_net_pnl"], baseline["net_pnl"]
        )
