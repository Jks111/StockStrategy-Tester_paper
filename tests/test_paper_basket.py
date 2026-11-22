import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_research import temporary

from quantlab.paper_basket import SYMBOLS, PaperBasket
from quantlab.storage import Store


class BasketTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(self.enterContext(temporary()))
        self.worker = PaperBasket(self.store)
        self.broker = Mock()
        self.positions = []
        self.clock = "2026-11-20T20:56:00+00:00"
        self.cash = "100000"
        self.fills = {}
        self.broker.get.side_effect = self.get
        self.broker.quotes.side_effect = lambda symbols: {
            "quotes": {s: {"t": self.clock, "bp": 100, "ap": 100.01} for s in symbols}
        }
        self.broker.history.return_value = []
        self.posted = []
        self.broker.order.side_effect = self.posted.append

    def get(self, path):
        if path == "/account":
            return {"id": "paper", "equity": "100000", "cash": self.cash}
        if path == "/clock":
            return {"is_open": True, "timestamp": self.clock}
        if path == "/positions":
            return self.positions
        if path.startswith("/orders:"):
            return self.fills[path.split("=")[1]]
        return []

    def cycle(self):
        with patch("quantlab.paper_basket.datetime", wraps=datetime) as dt:
            dt.now.return_value = datetime.fromisoformat(self.clock)
            self.worker.cycle(self.broker)

    def test_friday_allocations_pending_and_monday_exits(self):
        self.cycle()
        self.assertEqual(len(self.posted), 6)
        self.assertTrue(all(o["notional"] == "2400.00" for o in self.posted))
        for order in self.posted:
            self.fills[order["client_order_id"]] = {
                "status": "new",
                "filled_qty": "0",
                "filled_avg_price": None,
            }
        self.cycle()
        self.assertEqual(len(self.posted), 6)
        for fill in self.fills.values():
            fill.update(status="filled", filled_qty="24", filled_avg_price="100")
        self.positions = [{"symbol": s, "qty": "48"} for s in SYMBOLS]
        self.worker = PaperBasket(self.store)
        self.cycle()
        self.assertEqual(len(self.posted), 6)
        self.clock = "2026-11-23T14:30:30+00:00"
        self.cycle()
        self.assertEqual(len(self.posted), 12)
        self.assertTrue(all(o["side"] == "sell" and o["qty"] == "24" for o in self.posted[6:]))

    def test_performance_tracks_partial_fills_open_loss_and_daily_fees(self):
        with self.store.connect() as db:
            for cid, strategy, side, qty, price in [
                ("buy", "profit_target", "buy", "3.5", "100"),
                ("other", "mean_reversion", "buy", "1", "90"),
                ("partial", "profit_target", "sell", "1", "105"),
            ]:
                db.execute(
                    "INSERT INTO paper_basket_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        "paper",
                        strategy,
                        "AAPL",
                        "2026-11-20",
                        side,
                        "canceled" if cid == "partial" else "filled",
                        qty,
                        price,
                        0,
                        "test",
                    ),
                )
            self.worker.journal("broker", "filled", db=db, order_id="buy", filled_qty="3.5")
        self.assertIsNone(
            next(r for r in self.worker.view()["performance"] if r["strategy"] == "profit_target")[
                "net"
            ]
        )
        self.worker.journal(
            "decision",
            "Holding",
            "profit_target",
            "AAPL",
            quote={"bp": 96, "ap": 97, "t": self.clock},
        )
        view = self.worker.view()
        orders = {r["cid"]: r for r in view["orders"]}
        self.assertEqual(view["recent_realized_pnl"], 5)
        self.assertEqual(orders["partial"]["realized_pnl"], 5)
        self.assertIsNone(orders["buy"]["realized_pnl"])
        self.assertIsNone(orders["buy"]["executed_at"])
        self.assertTrue(orders["buy"]["observed_at"])
        rows = {r["strategy"]: r for r in view["performance"]}
        target = rows["profit_target"]
        self.assertEqual(
            (target["realized"], target["unrealized"], target["fees"], target["net"]),
            (5, -10, 0.02, -5.02),
        )
        self.assertEqual(target["holdings"][0]["qty"], "2.5")
        self.assertTrue(target["holdings"][0]["opened"])
        self.assertEqual(rows["mean_reversion"]["net"], 6)

    def test_reserved_cash_and_manual_positions(self):
        self.cash = "40500"
        self.cycle()
        self.assertEqual(sum(Decimal(o["notional"]) for o in self.posted), Decimal(500))
        self.positions = [{"symbol": "AAPL", "qty": "1"}]
        self.fills[self.posted[0]["client_order_id"]] = {
            "status": "canceled",
            "filled_qty": "0",
            "filled_avg_price": None,
        }
        self.cycle()
        self.assertIn("on hold", self.worker.pair_status["overnight", "AAPL"]["detail"])
        self.assertTrue(any(o["symbol"] == "AMZN" for o in self.posted))

    def test_full_manual_exit_waits_for_open_order_and_uses_final_fill(self):
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO paper_basket_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "buy",
                    "paper",
                    "profit_target",
                    "SPY",
                    "2026-11-20",
                    "buy",
                    "filled",
                    "2",
                    "100",
                    5,
                    "entry",
                ),
            )
        pending = True

        def get(path):
            if path == "/orders?status=open&limit=1":
                return [{"symbol": "SPY"}] if pending else []
            if path.startswith("/orders?status=closed"):
                return [
                    dict(client_order_id="buy", filled_at="2026-11-20T14:30:00Z"),
                    dict(
                        symbol="SPY",
                        id="sold",
                        side="sell",
                        status="filled",
                        filled_qty="2",
                        filled_avg_price="98",
                        filled_at="2026-11-20T18:00:00Z",
                    ),
                ]
            return self.get(path)

        self.broker.get.side_effect = get
        self.cycle()
        self.assertEqual(len(self.worker.view()["orders"]), 1)
        pending = False
        self.cycle()
        result = next(
            r for r in self.worker.view()["performance"] if r["strategy"] == "profit_target"
        )
        self.assertEqual(result["holdings"], [])
        self.assertEqual(result["realized"], -4)
        self.assertFalse(self.posted)

    def test_manual_sell_split_restart_and_same_session_entry_block(self):
        with self.store.connect() as db:
            for cid, strategy, qty in [("a", "mean_reversion", "1.5"), ("b", "profit_target", "2")]:
                db.execute(
                    "INSERT INTO paper_basket_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        "paper",
                        strategy,
                        "AAPL",
                        "2026-11-20",
                        "buy",
                        "filled",
                        qty,
                        "100",
                        5,
                        "entry",
                    ),
                )
        self.positions = [{"symbol": "AAPL", "qty": "0.75"}]

        def get(path):
            if path.startswith("/orders?status=closed"):
                return [
                    dict(id="broker-b", client_order_id="b", filled_at="2026-11-20T14:31:00Z"),
                    dict(
                        id="manual",
                        symbol="AAPL",
                        client_order_id="app",
                        status="filled",
                        side="sell",
                        filled_qty="2.75",
                        filled_avg_price="103",
                        filled_at="2026-11-20T20:40:00Z",
                    ),
                ]
            return self.get(path)

        self.broker.get.side_effect = get
        self.cycle()
        self.worker = PaperBasket(self.store)
        self.cycle()
        self.assertFalse(any(o["symbol"] == "AAPL" for o in self.posted))
        self.assertTrue(any(o["symbol"] == "SPY" for o in self.posted))
        view = self.worker.view()
        manual = [o for o in view["orders"] if o["reason"] == "Manual/external sale"]
        self.assertEqual(
            {o["strategy"]: Decimal(o["qty"]) for o in manual},
            {"mean_reversion": Decimal("1.5"), "profit_target": Decimal("1.25")},
        )
        self.assertTrue(all(o["executed_at"].startswith("2026-11-20T20:40:00") for o in manual))
        target = next(r for r in view["performance"] if r["strategy"] == "profit_target")
        self.assertEqual(target["realized"], 3.75)
        self.assertEqual(Decimal(target["holdings"][0]["qty"]), Decimal(".75"))

    def test_intraday_and_daily_entry_and_exit(self):
        from datetime import timedelta

        from quantlab.domain import Bar
        from quantlab.strategies import definition

        self.clock = "2026-11-20T14:31:00+00:00"
        now = datetime.fromisoformat(self.clock)

        def history(symbols, start, end, frame, feed, cutoff):
            rows = []
            for symbol in symbols:
                for i in range(25):
                    stamp = now - timedelta(days=25 - i)
                    price = 80 if i == 24 else 100
                    if frame == "5Min":
                        stamp = now.replace(minute=30) - timedelta(minutes=5 * (24 - i))
                        price = 105 if i == 24 else 100
                    rows.append(
                        Bar(
                            symbol,
                            stamp.isoformat(),
                            price,
                            price + 1,
                            price - 1,
                            price,
                            100,
                            stamp.date().isoformat(),
                            stamp.replace(hour=21).isoformat(),
                        )
                    )
            return rows

        self.broker.history.side_effect = history
        self.cycle()
        self.assertEqual(len(self.posted), 6)
        for o in self.posted:
            self.fills[o["client_order_id"]] = {
                "status": "canceled",
                "filled_qty": "0",
                "filled_avg_price": None,
            }
        self.clock = "2026-11-20T14:35:30+00:00"
        self.cycle()
        self.assertEqual(len(self.posted), 9)
        lot = {"avg": "100", "distance": 3, "day": "2026-11-20", "cid": "test"}
        for strategy, bid, remaining, expected in [
            ("profit_target", 101, 20, "Observed cash target"),
            ("mean_reversion", 96, 20, "Observed stop price"),
            ("opening_ema", 100, 4, "Session exit"),
        ]:
            self.assertEqual(
                self.worker.exit_reason(
                    strategy,
                    lot,
                    Decimal(5),
                    Decimal(bid),
                    [],
                    definition(strategy)["defaults"],
                    lot["day"],
                    remaining,
                ),
                expected,
            )
