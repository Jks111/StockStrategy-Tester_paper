import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quantlab.analytics import summarize
from quantlab.data.providers import Alpaca, import_legacy, parse_form4, parse_insider_csv
from quantlab.domain import Bar, Context, InsiderEvent, Signal, instant
from quantlab.engine import backtest
from quantlab.jobs import Service
from quantlab.storage import Store

TEST_ROOT = Path(__file__).resolve().parents[1] / "var" / "test-temp"
TEST_ROOT.mkdir(parents=True, exist_ok=True)


def temporary():
    return tempfile.TemporaryDirectory(dir=TEST_ROOT)


def bar(day, open=100, close=100, high=None, low=None, symbol="SPY"):
    return Bar(
        symbol,
        day + "T14:30:00+00:00",
        open,
        high or max(open, close) + 1,
        low or min(open, close) - 1,
        close,
        100000,
        day,
        day + "T21:00:00+00:00",
    )


def dataset(bars, timeframe="1Day", source="csv"):
    return {
        "id": "test",
        "source": source,
        "timeframe": timeframe,
        "start": bars[0].session,
        "end": bars[-1].session,
        "symbols": sorted({b.symbol for b in bars}),
        "quality": [],
        "hash": "test",
        "currency": "USD",
    }


def simulate(
    bars, strategy="buy_hold", params=None, risk=None, events=None, signal=None, liquidate=True
):
    meta = dataset(bars)
    groups = {}
    for b in bars:
        groups.setdefault(b.symbol, []).append(b)
    spec = {
        "strategy": strategy,
        "dataset": "test",
        "params": params or {},
        "risk": {
            "capital": 10000,
            "spread_bps": 0,
            "slippage_bps": 0,
            "borrow_apr": 0,
            **(risk or {}),
        },
    }
    if signal:
        with patch("quantlab.engine.load", return_value=signal):
            return backtest(spec, meta, groups, events or [], liquidate_end=liquidate)
    return backtest(spec, meta, groups, events or [], liquidate_end=liquidate)


class ExecutionTests(unittest.TestCase):
    def test_default_portfolio_starts_with_one_thousand(self):
        bars = [bar("2026-01-05"), bar("2026-01-06")]
        result = backtest(
            {"strategy": "buy_hold", "dataset": "test"}, dataset(bars), {"SPY": bars}, []
        )
        self.assertEqual(result["spec"]["risk"]["capital"], 1000)
        self.assertLess(result["metrics"]["end_equity"], 1000)
        self.assertGreater(result["metrics"]["end_equity"], 990)

    def test_signal_cannot_fill_same_daily_bar(self):
        bars = [
            bar("2026-01-05", close=110),
            bar("2026-01-06", open=120, close=125),
            bar("2026-01-07", open=125, close=130),
        ]
        t = simulate(bars)["trades"][0]
        self.assertEqual(t["entry_price"], 120)
        self.assertEqual(t["entry_time"], bars[1].time)
        self.assertEqual(t["signal_time"], bars[0].session_end)

    def test_ambiguous_stop_target_is_adverse(self):
        bars = [bar("2026-01-05"), bar("2026-01-06", high=110, low=90), bar("2026-01-07")]
        r = simulate(
            bars,
            signal=lambda c: (
                Signal(1, "test", stop_price=95, target_price=105) if c.index == 0 else None
            ),
        )
        self.assertEqual(r["trades"][0]["exit_price"], 95)
        self.assertLess(r["trades"][0]["net_pnl"], 0)

    def test_stop_gaps_fill_at_worse_open(self):
        bars = [
            bar("2026-01-05"),
            bar("2026-01-06", low=97),
            bar("2026-01-07", open=80, close=82, low=78),
        ]
        r = simulate(
            bars, signal=lambda c: Signal(1, "test", stop_price=95) if c.index == 0 else None
        )
        self.assertEqual(r["trades"][0]["exit_price"], 80)
        self.assertEqual(r["trades"][0]["exit_time"], bars[-1].time)

    def test_transaction_costs_reduce_flat_return(self):
        bars = [bar("2026-01-05"), bar("2026-01-06"), bar("2026-01-07")]
        r = simulate(bars, risk={"spread_bps": 4, "slippage_bps": 2, "commission": 1})
        t = r["trades"][0]
        self.assertAlmostEqual(t["entry_price"], 100.04)
        self.assertAlmostEqual(t["exit_price"], 99.96)
        self.assertLess(r["metrics"]["net_return"], 0)
        self.assertAlmostEqual(t["net_pnl"], -t["total_cost"])
        self.assertAlmostEqual(r["metrics"]["end_equity"], 10000 + t["net_pnl"])

    def test_limits_share_one_portfolio(self):
        bars = [
            bar(day, symbol=s)
            for day in ("2026-01-05", "2026-01-06", "2026-01-07")
            for s in ("AAPL", "MSFT", "SPY")
        ]
        r = simulate(bars, risk={"max_positions": 1})
        self.assertEqual(len(r["trades"]), 1)
        self.assertGreater(r["metrics"]["rejected_entries"], 0)
        self.assertTrue(all(p["positions"] <= 1 for p in r["equity"]))

    def test_pending_exit_precedes_new_decision(self):
        bars = [bar("2026-01-05"), bar("2026-01-06"), bar("2026-01-07", open=102)]

        def signal(c):
            return (
                Signal(1, "enter") if c.index == 0 else Signal(0, "exit") if c.index == 1 else None
            )

        r = simulate(bars, signal=signal)
        self.assertEqual(r["trades"][0]["exit_reason"], "exit")
        self.assertEqual(r["trades"][0]["exit_price"], 102)

    def test_open_replay_does_not_liquidate(self):
        bars = [bar("2026-01-05"), bar("2026-01-06", close=110)]
        r = simulate(bars, liquidate=False)
        self.assertEqual(len(r["open_positions"]), 1)
        self.assertEqual(r["trades"], [])
        self.assertEqual(r["equity"][-1]["positions"], 1)

    def test_negative_positions_not_dropped_at_end(self):
        bars = [bar("2026-01-05"), bar("2026-01-06", close=90)]
        r = simulate(bars)
        self.assertEqual(r["trades"][0]["exit_reason"], "End of test liquidation")
        self.assertLess(r["trades"][0]["net_pnl"], 0)

    def test_weekend_overnight_is_friday_to_monday(self):
        bars = [bar("2026-01-09", close=100), bar("2026-01-12", open=110, close=109)]
        r = simulate(bars, "overnight_weekend")
        self.assertEqual(len(r["trades"]), 1)
        t = r["trades"][0]
        self.assertEqual(t["entry_time"], bars[0].session_end)
        self.assertEqual(t["exit_time"], bars[1].time)
        self.assertEqual(t["exit_price"], 110)
        self.assertGreater(t["hold_hours"], 60)

    def test_drawdown_halt_blocks_reentry(self):
        bars = [
            bar("2026-01-05"),
            bar("2026-01-06", close=70),
            bar("2026-01-07", open=70, close=65),
        ]
        r = simulate(bars, risk={"position_fraction": 1, "max_drawdown": 0.1})
        self.assertTrue(r["metrics"]["drawdown_halt"])

    def test_trailing_stop_uses_next_bar(self):
        bars = [
            bar("2026-01-05"),
            bar("2026-01-06", high=150, low=99, close=140),
            bar("2026-01-07", open=140, high=141, low=135, close=136),
        ]
        r = simulate(
            bars,
            signal=lambda c: (
                Signal(1, "trail", stop_distance=10, exit_mode="trail") if c.index == 0 else None
            ),
        )
        # Second bar's new high cannot make a stop act earlier within that same bar.
        self.assertEqual(r["trades"][0]["exit_time"], bars[2].time)
        self.assertEqual(r["trades"][0]["exit_price"], 140)

    def test_cash_target_covers_modelled_fees(self):
        bars = [bar("2026-01-05"), bar("2026-01-06", high=110, low=99), bar("2026-01-07")]
        r = simulate(
            bars, "profit_target", risk={"spread_bps": 4, "slippage_bps": 2, "commission": 1}
        )
        self.assertAlmostEqual(r["trades"][0]["net_pnl"], 5, places=8)

    def test_close_after_stop_does_not_count_as_recovery(self):
        bars = [bar("2026-01-05"), bar("2026-01-06", close=102, low=90)]
        r = simulate(
            bars, signal=lambda c: Signal(1, "test", stop_price=95) if c.index == 0 else None
        )
        self.assertIsNone(r["trades"][0]["recovery_hours"])

    def test_short_equity_costs_and_collateral(self):
        day = "2026-01-05"
        bars = [
            Bar(
                "SPY",
                day + f"T14:{30 + i * 5:02d}:00+00:00",
                100,
                101,
                99,
                100,
                100000,
                day,
                day + "T21:00:00+00:00",
            )
            for i in range(3)
        ]
        spec = {
            "strategy": "opening_ema",
            "dataset": "test",
            "params": {"allow_short": True},
            "risk": {"capital": 10000, "spread_bps": 0, "slippage_bps": 0, "borrow_apr": 0.1},
        }
        with patch(
            "quantlab.engine.load",
            return_value=lambda c: Signal(-1, "short test") if c.index == 0 else None,
        ):
            r = backtest(spec, dataset(bars, "5Min"), {"SPY": bars}, [])
        t = r["trades"][0]
        self.assertEqual(t["side"], "short")
        self.assertLess(t["net_pnl"], 0)
        self.assertAlmostEqual(r["metrics"]["end_equity"], 10000 + t["net_pnl"])
        self.assertAlmostEqual(t["total_cost"], 2000 * 0.1 * 10 / (365 * 24 * 60))


class TimingTests(unittest.TestCase):
    def test_strategy_context_cannot_index_future_bars(self):
        observations = []

        def signal(ctx):
            observations.append(len(ctx.bars))
            with self.assertRaises(IndexError):
                ctx.bars[ctx.index + 1]
            self.assertEqual(list(ctx.history), list(ctx.bars))
            self.assertEqual(ctx.history[::-1], list(ctx.bars)[::-1])
            return None

        simulate([bar("2026-01-05"), bar("2026-01-06")], signal=signal)
        self.assertEqual(observations, [1, 2])

    def test_opening_ema_uses_new_york_time_in_winter_and_summer(self):
        from quantlab.strategies.opening_ema import evaluate

        for day, hour in (("2026-01-05", 14), ("2026-07-06", 13)):
            end = day + f"T{hour + 7:02d}:00:00+00:00"
            history = [
                Bar(
                    "SPY",
                    day + f"T{hour - 1:02d}:{i * 5:02d}:00+00:00",
                    95,
                    96,
                    94,
                    95,
                    1000,
                    day,
                    end,
                )
                for i in range(12)
            ]
            history.append(
                Bar("SPY", day + f"T{hour:02d}:30:00+00:00", 100, 102, 99, 101, 1000, day, end)
            )
            ctx = Context(
                history,
                12,
                [],
                {"ema_length": 12, "atr_multiple": 2},
                decision_time=day + f"T{hour:02d}:35:00+00:00",
            )
            self.assertEqual(evaluate(ctx).side, 1)
            history.append(
                Bar("SPY", day + f"T{hour:02d}:35:00+00:00", 101, 103, 100, 102, 1000, day, end)
            )
            ctx.index = 13
            self.assertIsNone(evaluate(ctx))

    def test_opening_range_target_from_boundary(self):
        from quantlab.strategies.opening_range import evaluate

        day = "2026-01-05"
        end = day + "T21:00:00+00:00"
        bars = [
            Bar("SPY", day + f"T14:{minute}:00+00:00", 100, 102, 98, 100, 1000, day, end)
            for minute in (30, 35, 40)
        ]
        bars.append(Bar("SPY", day + "T14:45:00+00:00", 100, 104, 99, 103, 1000, day, end))
        s = evaluate(
            Context(
                bars,
                3,
                [],
                {"atr_multiple": 0.25, "target_multiple": 2},
                decision_time=day + "T14:50:00+00:00",
            )
        )
        self.assertEqual(s.side, 1)
        self.assertEqual(s.target_price, 110)
        self.assertEqual(s.stop_price, 97)

    def test_insider_publication_not_transaction_price(self):
        bars = [
            bar("2026-01-05"),
            bar("2026-01-06"),
            bar("2026-01-07", open=110),
            bar("2026-01-08", open=115),
        ]
        e = InsiderEvent(
            "e",
            "SPY",
            "Buyer",
            "2025-12-30",
            "2026-01-06T22:00:00+00:00",
            "2026-01-07T00:00:00+00:00",
            "P",
            10000,
            1,
            "csv",
        )
        r = simulate(bars, "insider_purchase", events=[e])
        self.assertEqual(r["trades"][0]["entry_time"], bars[3].time)
        self.assertEqual(r["trades"][0]["entry_price"], 115)

    def test_unknown_availability_excluded(self):
        b = bar("2026-01-05")
        e = InsiderEvent("e", "SPY", "Buyer", "2025-12-30", None, b.time, "P", 10000, 10, "csv")
        self.assertEqual(Context([b], 0, [e], {}, decision_time=b.session_end).public_events(), [])
        with self.assertRaisesRegex(ValueError, "No usable"):
            simulate([b, bar("2026-01-06")], "insider_purchase", events=[e])

    def test_amended_and_future_events_excluded(self):
        b = bar("2026-01-05")
        base = {
            "event_id": "e",
            "symbol": "SPY",
            "insider": "Buyer",
            "transaction_date": "2025-12-30",
            "available_at": "2026-01-06T00:00:00+00:00",
            "observed_at": b.time,
            "code": "P",
            "shares": 10000,
            "price": 10,
            "source": "csv",
        }
        self.assertEqual(
            Context(
                [b], 0, [InsiderEvent(**base)], {}, decision_time=b.session_end
            ).public_events(),
            [],
        )
        base.update(available_at=b.time, amended=True)
        self.assertEqual(
            Context(
                [b], 0, [InsiderEvent(**base)], {}, decision_time=b.session_end
            ).public_events(),
            [],
        )

    def test_insider_overnight_uses_preclose_cutoff(self):
        bars = [bar("2026-01-09"), bar("2026-01-12")]
        e = InsiderEvent(
            "e",
            "SPY",
            "Buyer",
            "2026-01-09",
            "2026-01-09T20:58:00+00:00",
            bars[0].time,
            "P",
            10000,
            10,
            "csv",
        )
        r = simulate(bars, "insider_overnight", params={"weekday": "friday"}, events=[e])
        self.assertEqual(r["trades"], [])

    def test_naive_timestamps_rejected(self):
        with self.assertRaises(ValueError):
            instant("2026-01-05T09:30:00")


class DataTests(unittest.TestCase):
    def test_unknown_csv_code_not_assumed_purchase(self):
        e = parse_insider_csv(
            "symbol,insider,transaction_date,shares,price,available_at\nAAPL,Buyer,2026-01-01,20,100,2026-01-02T00:00:00Z\n"
        )[0]
        self.assertEqual(e.code, "UNKNOWN")

    def test_form4_metadata_and_non_purchase_codes(self):
        xml = """<ownershipDocument><documentType>4</documentType><issuer><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer><reportingOwner><reportingOwnerId><rptOwnerName>Buyer</rptOwnerName></reportingOwnerId></reportingOwner><nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-01-01</value></transactionDate><transactionCoding><transactionCode>S</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>10</value></transactionShares><transactionPricePerShare><value>100</value></transactionPricePerShare></transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
        e = parse_form4(xml, "accession", "2026-01-03T18:00:00Z")[0]
        self.assertEqual(e.symbol, "AAPL")
        self.assertEqual(e.code, "S")
        self.assertEqual(e.price, 100)
        self.assertEqual(e.available_at, "2026-01-03T18:00:00+00:00")

    def test_legacy_is_read_only_and_ineligible(self):
        with temporary() as root:
            p = Path(root) / "old.db"
            with sqlite3.connect(p) as c:
                c.execute(
                    "CREATE TABLE simulations(symbol,insider,snapshot_date,total_insider_shares,weighted_avg_price)"
                )
                c.execute("INSERT INTO simulations VALUES('AAPL','Buyer','2026-01-01',10,100)")
            c.close()
            before = p.read_bytes()
            events = import_legacy(p)
            self.assertEqual(p.read_bytes(), before)
            self.assertIsNone(events[0].available_at)
            self.assertEqual(events[0].code, "UNKNOWN")
            self.assertEqual(events[0].transaction_date, "unknown")
            self.assertEqual(events[0].snapshot_date, "2026-01-01")

    def test_dataset_validation_and_provenance_identity(self):
        with temporary() as root:
            s = Store(root)
            bars = [bar("2026-01-05")]
            a = s.save_bars(bars, {"source": "csv", "timeframe": "1Day", "feed": "one"})
            b = s.save_bars(bars, {"source": "csv", "timeframe": "1Day", "feed": "two"})
            self.assertNotEqual(a["id"], b["id"])
            self.assertEqual(s.load_bars(a["id"])["SPY"], bars)
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                s.save_bars(bars * 2, {})
            bad = bar("2026-01-05", high=float("inf"))
            with self.assertRaisesRegex(ValueError, "finite"):
                s.save_bars([bad], {})

    def test_alpaca_uses_calendar_pagination_and_offsets(self):
        pages = [
            [{"date": "2026-01-05", "open": "09:30", "close": "16:00"}],
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2026-01-05T05:00:00Z",
                            "o": 100,
                            "h": 101,
                            "l": 99,
                            "c": 100,
                            "v": 100,
                        }
                    ]
                },
                "next_page_token": "page2",
            },
            {
                "bars": {
                    "SPY": [
                        {
                            "t": "2026-01-06T05:00:00Z",
                            "o": 100,
                            "h": 101,
                            "l": 99,
                            "c": 100,
                            "v": 100,
                        }
                    ]
                },
                "next_page_token": None,
            },
        ]
        with (
            patch(
                "quantlab.data.providers.credentials",
                return_value={"ALPACA_API_KEY": "test", "ALPACA_API_SECRET": "test"},
            ),
            patch("quantlab.data.providers.request_json", side_effect=pages) as request,
            patch("quantlab.data.providers.time.sleep"),
        ):
            bars = Alpaca().history(["SPY"], "2026-01-05", "2026-01-06")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].time, "2026-01-05T14:30:00+00:00")
        self.assertIn("page_token=page2", request.call_args_list[-1].args[0])


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary()
        self.store = Store(self.temp.name)
        days = [(datetime(2025, 1, 1) + timedelta(days=i)).date().isoformat() for i in range(100)]
        self.metadata = self.store.save_bars(
            [bar(d) for d in days], {"source": "csv", "timeframe": "1Day"}
        )
        self.service = Service(self.store)

    def tearDown(self):
        self.service.pool.shutdown(wait=True)
        self.temp.cleanup()

    def spec(self):
        return {"dataset": self.metadata["id"], "strategy": "regression_trend"}

    def test_holdout_is_reserved_and_one_use(self):
        holdout = self.service.holdout(self.metadata)
        self.assertLess(self.service.prepare(self.spec())["end"], holdout["start"])
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.service.prepare({**self.spec(), "end": self.metadata["end"]})
        run_id = self.service.submit({**self.spec(), "split": "final_holdout"})
        with self.assertRaisesRegex(ValueError, "already used"):
            self.service.submit({**self.spec(), "split": "final_holdout"})
        self.service.pool.shutdown(wait=True)
        self.assertEqual(self.store.run(run_id)["status"], "complete")

    def test_grid_prevalidates_all_and_caps_count(self):
        with self.assertRaises(ValueError):
            self.service.batch(self.spec(), {"lookback": [10, -3]})
        self.assertEqual(self.store.runs(), [])
        with self.assertRaisesRegex(ValueError, "Maximum"):
            self.service.batch(self.spec(), {"lookback": list(range(2, 503))})

    def test_replay_prefix_is_deterministic(self):
        raw = {**self.spec(), "strategy": "buy_hold"}
        first = self.service.replay(raw)
        second = self.service.replay(advance=True)
        self.assertEqual(first["index"], 0)
        self.assertEqual(second["index"], 1)
        self.assertEqual(len(second["result"]["equity"]), 2)
        self.assertEqual(len(second["result"]["open_positions"]), 1)

    def test_walk_forward_windows_do_not_overlap(self):
        batch = self.service.walk_forward(self.spec())
        specs = [self.store.run(r)["spec"] for r in batch["runs"]]
        self.assertTrue(all(a["end"] < b["start"] for a, b in zip(specs, specs[1:])))


class AnalyticsTests(unittest.TestCase):
    def test_capm_beta_known_series(self):
        equity = []
        benchmark = []
        p = b = 100
        for i in range(50):
            x = 0.01 if i % 2 else -0.008
            b *= 1 + x
            p *= 1 + 2 * x
            d = (datetime(2025, 1, 1) + timedelta(days=i)).date().isoformat()
            equity.append({"session": d, "equity": p})
            benchmark.append({"session": d, "close": b})
        metrics = summarize(equity, [], 100, benchmark)
        self.assertAlmostEqual(metrics["beta"], 2, places=10)
        self.assertAlmostEqual(metrics["annualized_alpha"], 0, places=10)

    def test_no_trades_is_not_100_percent_win_rate(self):
        m = summarize([{"session": "2026-01-01", "equity": 100}], [], 100, [])
        self.assertIsNone(m["win_rate"])
        self.assertIsNone(m["profit_factor"])
        self.assertIsNone(m["sharpe"])


if __name__ == "__main__":
    unittest.main()
