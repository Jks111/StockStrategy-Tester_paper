"""Forward execution for the dashboard's fixed, long-only Alpaca paper basket."""

import hashlib
import json
import urllib.error
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN
from decimal import Decimal as D
from threading import Event, Lock, Thread

from .calendars import schedule, sessions
from .data.providers import NY, Alpaca, _RejectRedirects, request_json
from .domain import Context, instant
from .features import atr
from .strategies import definition, load
from .worker_performance import performance

SYMBOLS = ("AAPL", "AMZN", "SPY")
STRATEGIES = ("mean_reversion", "overnight", "profit_target", "overnight_weekend", "opening_ema")
BASE = "https://paper-api.alpaca.markets/v2"
DONE = {"filled", "canceled", "expired", "rejected"}
CENT = D(".01")


class SharesUnavailable(ValueError):
    """Alpaca rejected a sell because another order used the shares."""


class Broker(Alpaca):
    def get(self, path):
        return request_json(BASE + path, self.headers)

    def order(self, body):
        request = urllib.request.Request(
            BASE + "/orders",
            json.dumps(body).encode(),
            {**self.headers, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.build_opener(_RejectRedirects()).open(
                request, timeout=15
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if body["side"] == "sell" and error.code in (403, 422):
                detail = error.read(4096).lower()
                if b"insufficient qty" in detail or b"insufficient quantity" in detail:
                    raise SharesUnavailable("Shares unavailable for sale") from None
            raise ValueError(
                f"Paper broker returned HTTP {error.code}. Inspect orders before restarting."
            ) from None


def allocation(account, reserve):
    equity = D(account["equity"])
    floor = equity * D(reserve) / 100
    sleeve = min(equity, D(100000)) * (100 - reserve) / 500
    free = max(D(0), D(account["cash"]) - floor)
    return sleeve, free


class PaperBasket:
    def __init__(self, store, broker=Broker):
        self.store, self.broker = store, broker
        self.lock, self.stop = Lock(), Event()
        self.thread = None
        self.heartbeat = None
        self.message = "Stopped"
        self.reserve = 40
        self.pair_status = {}
        self.events = deque(maxlen=90)
        self.cache = {}
        self.account_summary = None
        self.account_id = None
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS paper_basket_orders (
                cid TEXT PRIMARY KEY, account TEXT, strategy TEXT, symbol TEXT, day TEXT,
                side TEXT, status TEXT, qty TEXT, avg TEXT, distance REAL, reason TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS paper_basket_trails
                (cid TEXT PRIMARY KEY, stop REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS paper_external_sales
                (account TEXT, broker_id TEXT, symbol TEXT, day TEXT,
                 PRIMARY KEY(account,broker_id))""")

            db.execute("""CREATE TABLE IF NOT EXISTS worker_journal (
                id INTEGER PRIMARY KEY, time TEXT NOT NULL, kind TEXT NOT NULL, strategy TEXT,
                symbol TEXT, message TEXT NOT NULL, context TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS journal_time ON worker_journal(time)")
            db.execute("""CREATE INDEX IF NOT EXISTS journal_decision_symbol ON
                       worker_journal(symbol,id) WHERE kind='decision'""")
            db.execute("""CREATE INDEX IF NOT EXISTS journal_broker_order ON
                       worker_journal(json_extract(context,'$.order_id'),id) WHERE kind='broker'""")
            for row in reversed(
                db.execute(
                    "SELECT time,message FROM worker_journal WHERE kind='worker' ORDER BY id DESC LIMIT 90"
                ).fetchall()
            ):
                self.events.appendleft(dict(row))

    def journal(self, kind, message, strategy=None, symbol=None, db=None, **context):
        values = (
            datetime.now(timezone.utc).isoformat(),
            kind,
            strategy,
            symbol,
            message,
            json.dumps(context),
        )
        sql = "INSERT INTO worker_journal(time,kind,strategy,symbol,message,context) VALUES(?,?,?,?,?,?)"
        if db is None:
            with self.store.connect() as connection:
                connection.execute(sql, values)
        else:
            db.execute(sql, values)

    def journal_day(self, day):
        from datetime import date, time
        from zoneinfo import ZoneInfo

        start = datetime.combine(date.fromisoformat(day), time.min, ZoneInfo("Europe/Helsinki"))
        bounds = tuple(
            t.astimezone(timezone.utc).isoformat() for t in (start, start + timedelta(days=1))
        )
        with self.store.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM worker_journal WHERE time>=? AND time<? ORDER BY id", bounds
                )
            ]

    def note(self, message):
        self.journal("worker", message)
        with self.lock:
            self.events.appendleft(
                {"time": datetime.now(timezone.utc).isoformat(), "message": message}
            )

    def stop_warnings(self):
        warnings = []
        now = datetime.now(timezone.utc)
        local = now.astimezone(NY)
        hours = schedule("XNYS", local.year).get(local.date().isoformat())
        if hours and hours[0] <= now < hours[1]:
            warnings.append("The US regular trading session is still open.")
        with self.store.connect() as db:
            outstanding = db.execute("""SELECT COUNT(*) FROM paper_basket_orders WHERE status NOT IN
                                ('filled','canceled','expired','rejected')""").fetchone()[0]
        if outstanding:
            warnings.append(f"{outstanding} local order(s) await final broker confirmation.")
        try:
            broker = self.broker()
            if broker.get("/orders?status=open&limit=1"):
                warnings.append("Alpaca has open paper orders.")
            if broker.get("/positions"):
                warnings.append(
                    "The paper account holds positions. Automated exits need the worker running."
                )
        except Exception:
            warnings.append("Could not verify paper orders and positions with Alpaca.")
        return warnings

    def view(self):
        with self.store.connect() as db:
            db.execute("BEGIN")
            latest = db.execute(
                "SELECT account FROM paper_basket_orders ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            account = self.account_id or (latest[0] if latest else None)
            results, sell_profits = performance(db, account, STRATEGIES, SYMBOLS)
            orders = [
                dict(r)
                for r in db.execute(
                    """
                    SELECT o.cid,o.strategy,o.symbol,o.day,o.side,o.status,o.qty,o.avg,o.reason,
                      (SELECT json_extract(j.context,'$.executed_at') FROM worker_journal j
                       WHERE j.kind='broker' AND json_extract(j.context,'$.order_id')=o.cid
                         AND json_extract(j.context,'$.executed_at') IS NOT NULL
                       ORDER BY j.id DESC LIMIT 1) AS executed_at,
                      (SELECT j.time FROM worker_journal j
                       WHERE j.kind='broker' AND json_extract(j.context,'$.order_id')=o.cid
                         AND CAST(json_extract(j.context,'$.filled_qty') AS REAL)>0
                       ORDER BY j.id DESC LIMIT 1) AS observed_at
                    FROM paper_basket_orders o WHERE o.account=? ORDER BY o.rowid DESC LIMIT 20
                    """,
                    (account,),
                )
            ]
            closed_profits = []
            for order in orders:
                profit = sell_profits.get(order["cid"]) if order["status"] in DONE else None
                order["realized_pnl"] = float(profit) if profit is not None else None
                if profit is not None:
                    closed_profits.append(profit)
        with self.lock:
            return {
                "running": bool(self.thread and self.thread.is_alive()),
                "message": self.message,
                "events": list(self.events),
                "pairs": list(self.pair_status.values()),
                "heartbeat": self.heartbeat,
                "reserve": self.reserve,
                "account": self.account_summary,
                "orders": orders,
                "recent_realized_pnl": float(sum(closed_profits)) if closed_profits else None,
                "performance": results,
            }

    def command(self, body):
        with self.lock:
            if body["action"] == "stop":
                self.stop.set()
                return
            if body["action"] != "start" or self.thread and self.thread.is_alive():
                raise ValueError("A paper session is already running or the action is invalid")
            reserve = int(body["reserve"])
            if not 20 <= reserve <= 90:
                raise ValueError("Choose a cash reserve between 20% and 90%")
            self.reserve = reserve
            self.heartbeat = None
            self.stop.clear()
            self.message = "Connecting to Alpaca paper"
            self.thread = Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self):
        try:
            broker = self.broker()
            while not self.stop.is_set():
                self.message = self.cycle(broker)
                self.note(self.message)
                self.heartbeat = datetime.now(timezone.utc).isoformat()
                if self.stop.wait(20):
                    break
        except Exception as error:
            self.message = f"Paused: {error}"
            self.note(self.message)

    def ledger(self, broker, account):
        with self.store.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM paper_basket_orders WHERE account=? ORDER BY rowid", (account,)
                )
            ]
            for r in rows:
                if r["status"] in DONE:
                    continue
                order = broker.get("/orders:by_client_order_id?client_order_id=" + r["cid"])
                previous = (r["status"], r["qty"], r["avg"])
                r.update(
                    status=order["status"],
                    qty=order["filled_qty"] or "0",
                    avg=order["filled_avg_price"] or "0",
                )
                if previous != (r["status"], r["qty"], r["avg"]):
                    self.journal(
                        "broker",
                        r["status"],
                        r["strategy"],
                        r["symbol"],
                        db=db,
                        order_id=r["cid"],
                        side=r["side"],
                        filled_qty=r["qty"],
                        average_fill=r["avg"],
                        executed_at=order.get("filled_at"),
                    )
                db.execute(
                    "UPDATE paper_basket_orders SET status=?,qty=?,avg=? WHERE cid=?",
                    (r["status"], r["qty"], r["avg"], r["cid"]),
                )
        return rows

    def history(self, broker, day, frame, bucket, now):
        key = (day, frame, bucket)
        if key not in self.cache:
            start = now.date() - timedelta(days=120 if frame == "1Day" else 10)
            bars = broker.history(list(SYMBOLS), start.isoformat(), day, frame, "iex", cutoff=now)
            self.cache = {k: v for k, v in self.cache.items() if k[0] == day and k[1] != frame}
            self.cache[key] = {
                s: sorted(
                    (b for b in bars if b.symbol == s and (frame != "1Day" or b.session < day)),
                    key=lambda b: b.time,
                )
                for s in SYMBOLS
            }
        return self.cache[key]

    def import_external_sales(self, broker, account, symbol, missing, rows, quantities):
        """Import only terminal external sells that exactly explain the share deficit."""
        history = broker.get(f"/orders?status=closed&symbols={symbol}&limit=500")
        local = [
            r
            for r in rows
            if r["symbol"] == symbol and D(r["qty"]) > 0 and not r["cid"].startswith("external-")
        ]
        if not local or len(history) >= 500:
            return False
        known = {r["cid"] for r in rows}
        anchor = next(
            (o.get("filled_at") for o in history if o.get("client_order_id") == local[-1]["cid"]),
            None,
        )
        if not anchor:
            return False
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            seen = {
                r[0]
                for r in db.execute(
                    "SELECT broker_id FROM paper_external_sales WHERE account=? AND symbol=?",
                    (account, symbol),
                )
            }
            sales = []
            for order in history:
                if (
                    order.get("symbol") != symbol
                    or order.get("client_order_id") in known
                    or order["id"] in seen
                ):
                    continue
                qty = D(order.get("filled_qty") or "0")
                if not qty:
                    continue
                stamp = order.get("filled_at") or order.get("updated_at")
                if not stamp:
                    return False
                if instant(stamp) <= instant(anchor):
                    continue
                price = D(order.get("filled_avg_price") or "0")
                if order.get("side") != "sell" or order.get("status") not in DONE or price <= 0:
                    return False
                sales.append((instant(stamp), order["id"], qty, price))
            if sum(q for _, _, q, _ in sales) != missing:
                return False
            # Each strategy holds at most one open lot per ticker. Oldest entry first.
            entry_order = {r["strategy"]: i for i, r in enumerate(local) if r["side"] == "buy"}
            available = {s: quantities[s, symbol] for s in sorted(entry_order, key=entry_order.get)}
            for stamp, broker_id, qty, price in sorted(sales):
                day = stamp.astimezone(NY).date().isoformat()
                db.execute(
                    "INSERT INTO paper_external_sales VALUES(?,?,?,?)",
                    (account, broker_id, symbol, day),
                )
                for strategy in available:
                    take = min(available[strategy], qty)
                    if take <= 0:
                        continue
                    cid = f"external-{account}-{broker_id}-{strategy}"
                    db.execute(
                        "INSERT INTO paper_basket_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            cid,
                            account,
                            strategy,
                            symbol,
                            day,
                            "sell",
                            "filled",
                            str(take),
                            str(price),
                            0,
                            "Manual/external sale",
                        ),
                    )
                    self.journal(
                        "broker",
                        "Manual/external sale",
                        strategy,
                        symbol,
                        db=db,
                        order_id=cid,
                        side="sell",
                        filled_qty=str(take),
                        average_fill=str(price),
                        broker_order_id=broker_id,
                        executed_at=stamp.isoformat(),
                    )
                    available[strategy] -= take
                    qty -= take
        return True

    def cycle(self, broker):
        self.note("Reconciling paper account and order fills")
        acct = broker.get("/account")
        sleeve, available = allocation(acct, self.reserve)
        self.account_summary = {
            "equity": float(acct["equity"]),
            "cash": float(acct["cash"]),
            "per_strategy": float(sleeve),
            "available": float(available),
        }
        account = acct["id"]
        self.account_id = account
        rows = self.ledger(broker, account)
        if any(r["status"] not in DONE for r in rows):
            return "Waiting for paper order settlement"
        clock = broker.get("/clock")
        if acct.get("trading_blocked") or acct.get("account_blocked"):
            raise ValueError("Alpaca paper account is blocked")
        if broker.get("/orders?status=open&limit=1"):
            return "Waiting for existing account orders"
        now = instant(clock["timestamp"]).astimezone(NY)
        day = now.date().isoformat()
        quantities = defaultdict(D)
        cash_used = defaultdict(D)
        buys = {}
        for r in rows:
            q = D(r["qty"])
            sign = 1 if r["side"] == "buy" else -1
            key = (r["strategy"], r["symbol"])
            quantities[key] += sign * q
            cash_used[key[0]] += sign * q * D(r["avg"])
            if sign == 1 and q:
                buys[key] = r
        positions = {p["symbol"]: D(p["qty"]) for p in broker.get("/positions")}
        blocked = {}
        for symbol in SYMBOLS:
            owned = sum(quantities[s, symbol] for s in STRATEGIES)
            actual = positions.get(symbol, D(0))
            if actual != owned:
                if 0 <= actual < owned:
                    try:
                        imported = self.import_external_sales(
                            broker, account, symbol, owned - actual, rows, quantities
                        )
                    except ValueError:
                        imported = False
                    if imported:
                        return f"Recorded external sale · {symbol} · {owned - actual} shares"
                blocked[symbol] = (
                    f"{symbol} on hold: broker has {actual} shares, ledger has {owned}. Retrying."
                )
        warnings = " · " + " · ".join(blocked.values()) if blocked else ""
        if not clock["is_open"]:
            return "Market closed · waiting for next regular session" + warnings
        opening, closing = schedule("XNYS", now.year)[day]
        minutes = (now - opening).total_seconds() / 60
        remaining = (closing - now).total_seconds() / 60
        with self.store.connect() as db:
            manual_today = {
                r[0]
                for r in db.execute(
                    "SELECT symbol FROM paper_external_sales WHERE account=? AND day=?",
                    (account, day),
                )
            }
        self.note("Reading IEX quotes and completed bars")
        quotes = broker.quotes(list(SYMBOLS))["quotes"]
        daily = self.history(broker, day, "1Day", 0, now)
        intra = self.history(broker, day, "5Min", int(minutes // 5), now)
        sleeve, available = allocation(acct, self.reserve)
        prior_days = sessions(
            "XNYS",
            (now.date() - timedelta(days=14)).isoformat(),
            (now.date() - timedelta(days=1)).isoformat(),
        )
        prior_session = max(prior_days)
        submitted = 0
        sides = {}
        pairs = sorted(
            ((s, t) for s in STRATEGIES for t in SYMBOLS), key=lambda pair: quantities[pair] <= 0
        )
        for strategy, symbol in pairs:
            if self.stop.is_set():
                return "Stopping"

            def report(detail):
                self.journal(
                    "decision",
                    detail,
                    strategy,
                    symbol,
                    quote=quote,
                    shares=str(quantities[strategy, symbol]),
                    available_cash=str(available),
                    strategy_budget=str(sleeve),
                    reserve_pct=self.reserve,
                )
                with self.lock:
                    self.pair_status[strategy, symbol] = {
                        "strategy": strategy,
                        "symbol": symbol,
                        "detail": detail,
                        "time": now.isoformat(),
                    }

            quote = quotes.get(symbol)
            if symbol in blocked:
                report(blocked[symbol])
                continue
            if symbol in manual_today and quantities[strategy, symbol] == 0:
                report("New entries paused for session after external sale")
                continue
            if (
                not quote
                or not 0 <= (datetime.now(timezone.utc) - instant(quote["t"])).total_seconds() <= 60
            ):
                report("Waiting for a fresh quote")
                continue
            bid, ask = D(str(quote["bp"])), D(str(quote["ap"]))
            if not 0 < bid <= ask:
                report("Invalid bid/ask spread")
                continue
            key = (strategy, symbol)
            qty = quantities[key]
            buy = buys.get(key)
            bars = intra[symbol] if strategy == "opening_ema" else daily[symbol]
            params = definition(strategy)["defaults"]
            reason = None
            distance = 0
            if qty > 0:
                reason = self.exit_reason(strategy, buy, qty, bid, bars, params, day, remaining)
                report(reason or "Holding · monitoring exit")
                side = "sell"
                amount = {"qty": format(qty, "f")}
            else:
                report("Waiting for entry window")
                if strategy.startswith("overnight"):
                    if (
                        not 0.5 < remaining <= 5
                        or strategy == "overnight_weekend"
                        and now.weekday() != 4
                    ):
                        continue
                    reason = "Scheduled near-close entry"
                else:
                    if strategy == "opening_ema":
                        if not 5 <= minutes < 10 or not bars or instant(bars[-1].time) != opening:
                            continue
                    elif not 0 <= minutes < 5 or not bars or bars[-1].session != prior_session:
                        continue
                    if len(bars) < max(params.get("lookback", 0), 15):
                        continue
                    signal = load(strategy)(Context(bars, len(bars) - 1, [], params))
                    if not signal or signal.side != 1:
                        report("No eligible entry signal")
                        continue
                    reason, distance = signal.reason, signal.stop_distance
                side = "buy"
                budget = min(sleeve * D(".2"), sleeve - cash_used[strategy], available)
                if distance > 0:
                    budget = min(budget, sleeve * D(".01") * ask / D(str(distance)))
                budget = budget.quantize(CENT, rounding=ROUND_DOWN)
                if budget < 1:
                    report("Cash or risk limit reached")
                    continue
                amount = {"notional": str(budget)}
            if reason and symbol in sides and sides[symbol] != side:
                report("Deferred opposing order in same check")
                continue
            if not reason:
                continue
            # Include entry identity so closing an old lot cannot collide with a later exit.
            identity = f"{account}/{strategy}/{symbol}/{day}/{side}/" + (
                buy["cid"] if side == "sell" else ""
            )
            if side == "sell":
                # A confirmed quantity rejection can be retried after reconciliation.
                failures = sum(
                    r["strategy"] == strategy
                    and r["symbol"] == symbol
                    and r["day"] == day
                    and r["reason"] == "Shares unavailable"
                    for r in rows
                )
                if failures:
                    identity += f"/retry-{failures}"
            cid = "ql-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
            with self.store.connect() as db:
                inserted = db.execute(
                    "INSERT OR IGNORE INTO paper_basket_orders VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        account,
                        strategy,
                        symbol,
                        day,
                        side,
                        "intent",
                        "0",
                        "0",
                        distance,
                        reason,
                    ),
                ).rowcount
                if inserted:
                    self.journal(
                        "intent", reason, strategy, symbol, db=db, order_id=cid, side=side, **amount
                    )
            if not inserted:
                report("Order already recorded")
                continue
            try:
                broker.order(
                    {
                        "symbol": symbol,
                        "side": side,
                        "type": "market",
                        "time_in_force": "day",
                        "client_order_id": cid,
                        **({"position_intent": "sell_to_close"} if side == "sell" else {}),
                        **amount,
                    }
                )
            except SharesUnavailable:
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE paper_basket_orders SET status='rejected',reason='Shares unavailable' WHERE cid=?",
                        (cid,),
                    )
                return f"{symbol} sell rejected: shares unavailable · rechecking broker positions"
            if side == "buy":
                available -= budget
                cash_used[strategy] += budget
            report(f"Submitted {side}: {reason}")
            sides[symbol] = side
            self.note(f"Submitted {side} · {symbol} · {strategy} · {reason}")
            submitted += 1
        return (
            f"{now:%H:%M:%S} New York · checked 15 combinations · {submitted} paper orders"
            + warnings
        )

    def exit_reason(self, strategy, buy, qty, bid, bars, params, day, remaining):
        if strategy.startswith("overnight"):
            return "Next available session" if buy["day"] < day else None
        entry = D(buy["avg"])
        stop = float(entry) - buy["distance"]
        if strategy == "opening_ema":
            if buy["day"] < day or remaining <= 5:
                return "Session exit"
            if bars:
                from .features import ema

                line = ema([b.close for b in bars[-200:]], 12)
                if line and bars[-1].close <= line:
                    return "EMA momentum lost"
                # Only ratchet from completed bars after the actual entry session's opening signal.
                if (
                    bars[-1].session == day
                    and instant(bars[-1].time).astimezone(NY).time().isoformat() >= "09:40:00"
                ):
                    stop = max(stop, bars[-1].high - (atr(bars) or buy["distance"] / 2) * 2)
            with self.store.connect() as db:
                db.execute(
                    "INSERT INTO paper_basket_trails VALUES(?,?) ON CONFLICT(cid) DO UPDATE SET stop=MAX(stop,excluded.stop)",
                    (buy["cid"], stop),
                )
                stop = db.execute(
                    "SELECT stop FROM paper_basket_trails WHERE cid=?", (buy["cid"],)
                ).fetchone()[0]
        if float(bid) <= stop:
            return "Observed stop price"
        if strategy == "profit_target" and (bid - entry) * qty >= D(str(params["target_cash"])):
            return "Observed cash target"
        if sum(b.session >= buy["day"] for b in bars) >= params.get("hold_bars", 10**9):
            return "Holding time limit"
        if strategy == "mean_reversion" and bars:
            signal = load(strategy)(Context(bars, len(bars) - 1, [], params, position=True))
            if signal and signal.side == 0:
                return signal.reason
        return None
