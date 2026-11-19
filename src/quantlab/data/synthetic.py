"""Deterministic fabricated prices for exercising software, never market evidence."""

import math
import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..domain import Bar, InsiderEvent
from ..storage import digest

NY = ZoneInfo("America/New_York")


def build_sample(store):
    if any(d["source"] == "synthetic" for d in store.datasets()):
        return
    rng = random.Random(212)
    symbols = {"SPY": 430, "QQQ": 360, "AAPL": 180, "MSFT": 320, "NVDA": 80, "AMZN": 140}
    current = dict(symbols)
    day = date(2023, 1, 3)
    daily, intraday, events = [], [], []
    sessions = []
    while day <= date(2026, 10, 7):
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    for index, day in enumerate(sessions):
        market = rng.gauss(0.00025, 0.009)
        for symbol in symbols:
            open_price = current[symbol] * math.exp(rng.gauss(0.0002, 0.004))
            close = open_price * math.exp(market + rng.gauss(0, 0.007))
            high = max(open_price, close) * (1 + abs(rng.gauss(0, 0.004)))
            low = min(open_price, close) * (1 - abs(rng.gauss(0, 0.004)))
            opening = datetime.combine(day, datetime.min.time(), NY).replace(hour=9, minute=30)
            end = opening.replace(hour=16, minute=0)
            daily.append(
                Bar(
                    symbol,
                    opening.astimezone(timezone.utc).isoformat(),
                    open_price,
                    high,
                    low,
                    close,
                    rng.randint(5_000_000, 50_000_000),
                    day.isoformat(),
                    end.astimezone(timezone.utc).isoformat(),
                )
            )
            current[symbol] = close
            if index >= len(sessions) - 65:
                p = open_price
                for minute in range(78):
                    t = opening + timedelta(minutes=minute * 5)
                    nxt = p * math.exp((math.log(close / open_price) / 78) + rng.gauss(0, 0.0012))
                    hi, lo = max(p, nxt) * 1.0004, min(p, nxt) * 0.9996
                    intraday.append(
                        Bar(
                            symbol,
                            t.astimezone(timezone.utc).isoformat(),
                            p,
                            hi,
                            lo,
                            nxt,
                            rng.randint(40_000, 900_000),
                            day.isoformat(),
                            end.astimezone(timezone.utc).isoformat(),
                        )
                    )
                    p = nxt
            if symbol != "SPY" and index % 23 == 0:
                for person in ("Director A", "Director B"):
                    available = end + timedelta(hours=2)
                    event_id = "sample-" + digest([symbol, day.isoformat(), person])[:20]
                    events.append(
                        InsiderEvent(
                            event_id,
                            symbol,
                            person,
                            day.isoformat(),
                            available.isoformat(),
                            available.isoformat(),
                            "P",
                            1500,
                            open_price,
                            "synthetic",
                            "Director",
                        )
                    )
    quality = [
        "FABRICATED PRICES: software demonstration only",
        "Weekday calendar; synthetic sample includes holidays",
        "No corporate actions",
    ]
    store.save_bars(
        daily,
        {
            "name": "Sample · daily",
            "source": "synthetic",
            "feed": "fabricated",
            "timeframe": "1Day",
            "adjustment": "none",
            "quality": quality,
        },
    )
    store.save_bars(
        intraday,
        {
            "name": "Sample · 5 minute",
            "source": "synthetic",
            "feed": "fabricated",
            "timeframe": "5Min",
            "adjustment": "none",
            "quality": quality,
        },
    )
    store.put_events(events)
