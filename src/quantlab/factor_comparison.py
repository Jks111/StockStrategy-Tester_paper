"""Paired, forward-only comparison of cash-target and factor-residual exits."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from math import log

import numpy as np

from .calendars import sessions
from .data.providers import NY, Alpaca
from .domain import instant
from .features import atr
from .worker_performance import CAT_RATE, CENT, SEC_RATE

SECTORS = {"AAPL": "XLK", "AMZN": "XLY"}
RULES = ("Fixed $5", "Factor residual")
COST = 0.0004  # Half-spread plus slippage per side, as in the default backtest.


def fit_model(days, daily, stock):
    symbols = (stock, "SPY", SECTORS[stock])
    closes = np.array(
        [
            [daily.get((day, s)).close if (day, s) in daily else np.nan for s in symbols]
            for day in days[-61:]
        ]
    )
    if len(closes) != 61 or not np.all(np.isfinite(closes) & (closes > 0)):
        return None
    returns = np.diff(np.log(closes), axis=0)
    y, factors = returns[:, 0], returns[:, 1:]
    betas, _, rank, _ = np.linalg.lstsq(factors, y, rcond=None)
    if rank != 2:
        return None
    threshold = float(np.quantile(y - factors @ betas, 0.9))
    if threshold <= 0:
        return None
    return (*map(float, betas), threshold)


class Portfolio:
    def __init__(self):
        self.cash = self.peak = 1000.0
        self.drawdown = self.fees = 0.0
        self.positions = {}
        self.trades = []
        self.accrual = defaultdict(Decimal)

    def fill(self, day, qty, price, sell=False):
        q = Decimal(str(qty))
        for kind, amount in (
            ("CAT", q * CAT_RATE),
            ("SEC", q * Decimal(str(price)) * SEC_RATE if sell else Decimal(0)),
        ):
            key = (day, kind)
            before = self.accrual[key]
            self.accrual[key] += amount
            delta = float(
                self.accrual[key].quantize(CENT, rounding=ROUND_CEILING)
                - before.quantize(CENT, rounding=ROUND_CEILING)
            )
            self.cash -= delta
            self.fees += delta
        self.cash += (1 if sell else -1) * qty * price

    def equity(self, marks):
        return self.cash + sum(p["qty"] * marks[s] for s, p in self.positions.items())


def compare(daily_bars, intraday, calendar, days):
    daily = {(b.session, b.symbol): b for b in daily_bars}
    timeline = defaultdict(dict)
    for b in intraday:
        timeline[b.time][b.symbol] = b
    portfolios = {rule: Portfolio() for rule in RULES}
    entries = 0
    marks = {}
    skipped = 0
    history = sorted(calendar)
    last = None
    for timestamp, bars in sorted(timeline.items()):
        bar = next(iter(bars.values()))
        day = bar.session
        if day not in days:
            continue
        start = instant(timestamp)
        last = start + timedelta(minutes=5)
        index = history.index(day)
        for stock, sector in SECTORS.items():
            if stock not in bars:
                continue
            b = bars[stock]
            aligned = "SPY" in bars and sector in bars
            marks[stock] = b.close * (1 - COST)
            for book in portfolios.values():
                pos = book.positions.get(stock)
                if pos and pos.get("pending"):
                    price = b.open * (1 - COST)
                    book.fill(day, pos["qty"], price, sell=True)
                    book.trades.append(
                        dict(
                            symbol=stock,
                            entry=pos["time"],
                            exit=timestamp,
                            reason=pos["pending"],
                            gross=(price - pos["entry"]) * pos["qty"],
                            hours=(start - instant(pos["time"])).total_seconds() / 3600,
                        )
                    )
                    del book.positions[stock]
            if start == calendar[day][0] and all(
                stock not in book.positions for book in portfolios.values()
            ):
                model = fit_model(history[:index], daily, stock) if aligned else None
                if model:
                    price = b.open * (1 + COST)
                    budget = min(490.0, *(book.cash - 0.02 for book in portfolios.values()))
                    if budget >= 1:
                        qty = budget / price
                        distance = 3 * atr([daily[d, stock] for d in history[index - 15 : index]])
                        for book in portfolios.values():
                            book.fill(day, qty, price)
                            book.positions[stock] = dict(
                                entry=price,
                                qty=qty,
                                time=timestamp,
                                day_index=index,
                                stop=price - distance,
                                model=model,
                                stock_open=b.open,
                                market=bars["SPY"].open,
                                sector=bars[sector].open,
                            )
                        entries += 1
                else:
                    skipped += 1
            for rule, book in portfolios.items():
                p = book.positions.get(stock)
                if not p:
                    continue
                if marks[stock] <= p["stop"]:
                    p["pending"] = "volatility stop"
                elif index - p["day_index"] >= 60:
                    p["pending"] = "holding limit"
                elif rule == RULES[0] and (marks[stock] - p["entry"]) * p["qty"] >= 5:
                    p["pending"] = "$5 target"
                elif rule == RULES[1] and aligned:
                    market_beta, sector_beta, boundary = p["model"]
                    residual = (
                        log(b.close / p["stock_open"])
                        - market_beta * log(bars["SPY"].close / p["market"])
                        - sector_beta * log(bars[sector].close / p["sector"])
                    )
                    if residual >= boundary:
                        p["pending"] = "factor residual"
        for book in portfolios.values():
            equity = book.equity(marks)
            book.peak = max(book.peak, equity)
            book.drawdown = max(book.drawdown, 1 - equity / book.peak)
    if not entries:
        raise ValueError("No matched openings with sufficient factor history.")
    rows = []
    for rule, book in portfolios.items():
        open_positions = [
            dict(
                symbol=s,
                hours=(last - instant(p["time"])).total_seconds() / 3600,
                pnl=p["qty"] * (marks[s] - p["entry"]),
            )
            for s, p in book.positions.items()
        ]
        hours = [t["hours"] for t in book.trades] + [p["hours"] for p in open_positions]
        rows.append(
            dict(
                rule=rule,
                net=book.equity(marks) - 1000,
                fees=book.fees,
                drawdown=book.drawdown,
                mean_hours=sum(hours) / len(hours),
                open=open_positions,
                trades=book.trades,
            )
        )
    return dict(
        start=min(days), end=max(days), paired_entries=entries, skipped_openings=skipped, rows=rows
    )


def run_comparison():
    now = datetime.now(timezone.utc)
    today = now.astimezone(NY).date()
    calendar = sessions("XNYS", (today - timedelta(days=260)).isoformat(), today.isoformat())
    completed = sorted(
        day for day, (_, close) in calendar.items() if close < now - timedelta(minutes=16)
    )
    days = completed[-80:]
    symbols = sorted({"SPY", *SECTORS, *SECTORS.values()})
    api = Alpaca()
    daily = api.history(symbols, completed[-141], days[-1], "1Day", "iex", cutoff=now)
    bars = api.history(symbols, days[0], days[-1], "5Min", "iex", cutoff=now)
    return compare(daily, bars, calendar, days)
