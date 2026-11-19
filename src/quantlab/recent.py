"""Build fresh Alpaca snapshots for exploratory stock backtests."""

import re
from datetime import datetime, timedelta, timezone
from itertools import groupby

from .calendars import sessions
from .data.providers import NY, Alpaca
from .domain import Bar, instant
from .strategies import definition

PERIOD_DAYS = {"today": 1, "1w": 7, "2w": 14, "3w": 21, "4w": 28}
BAR_INTERVAL = timedelta(minutes=5)


def daily_bars(bars, schedule, cutoff):
    for (symbol, session), rows in groupby(bars, key=lambda bar: (bar.symbol, bar.session)):
        open_time, close_time = schedule[session]
        if close_time > cutoff:
            continue
        rows = list(rows)
        yield Bar(
            symbol,
            open_time.isoformat(),
            rows[0].open,
            max(bar.high for bar in rows),
            min(bar.low for bar in rows),
            rows[-1].close,
            sum(bar.volume for bar in rows),
            session,
            close_time.isoformat(),
        )


def selection(raw):
    if not isinstance(raw, dict):
        raise ValueError("Expected a recent backtest request object")
    symbols = raw.get("symbols", [raw.get("symbol")])
    strategies = raw.get("strategies", [raw.get("strategy")])
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("Choose at least one stock symbol")
    symbols = [s.strip().upper() if isinstance(s, str) else "" for s in symbols]
    if any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", s) for s in symbols):
        raise ValueError("Enter valid US ticker symbols")
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("Choose at least one strategy")
    strategies = {s["id"]: s for s in map(definition, strategies)}
    period, feed = raw.get("period"), raw.get("feed")
    if not isinstance(period, str) or period not in PERIOD_DAYS:
        raise ValueError("Choose today, 1w, 2w, 3w or 4w")
    if feed not in ("iex", "sip"):
        raise ValueError("Choose the IEX or SIP feed")
    return list(dict.fromkeys(symbols)), list(strategies.values()), period, feed


def incompatibility(symbol, strategy, period):
    if strategy["timeframe"] == "1Day" and period == "today":
        return "This strategy needs completed daily bars. Choose a longer period."
    if strategy["id"] == "residual_reversion" and symbol == "SPY":
        return "Residual reversion needs a stock other than the SPY benchmark."
    return None


def snapshot(raw):
    (symbol,), (strategy,), period, feed = selection(raw)
    reason = incompatibility(symbol, strategy, period)
    if reason:
        raise ValueError(reason)
    bars, metadata, spec = snapshot_data(
        symbol, strategy, period, feed, datetime.now(timezone.utc), download
    )
    return bars, metadata, {**spec, "strategy": strategy["id"]}


def download(symbols, start, end, feed, cutoff):
    return Alpaca().history(symbols, start, end, "5Min", feed, cutoff=cutoff)


def batch_history(extra_days=0):
    downloads = {}

    def history(symbols, start, end, feed, cutoff):
        for symbol in symbols:
            key = symbol, cutoff
            if key in downloads and (start < downloads[key][0] or end > downloads[key][1]):
                del downloads[key]
            if key not in downloads:
                earliest = (
                    (datetime.fromisoformat(start) - timedelta(days=extra_days)).date().isoformat()
                )
                try:
                    rows = download([symbol], earliest, end, feed, cutoff)
                except Exception as error:
                    rows = error
                downloads[key] = earliest, end, rows
            _, _, rows = downloads[key]
            if isinstance(rows, Exception):
                raise rows
            yield from (bar for bar in rows if start <= bar.session <= end)

    return history


def batch_clock():
    return datetime.now(timezone.utc)


def snapshot_data(symbol, strategy, period, feed, as_of, history, *, days_ago=0):
    daily = strategy["timeframe"] == "1Day"
    # Allow a margin over the 15-minute SIP restriction on basic Alpaca accounts.
    cutoff = as_of - timedelta(minutes=16) if feed == "sip" else as_of
    today = as_of.astimezone(NY).date() - timedelta(days=days_ago)
    days = PERIOD_DAYS[period] - 1
    warmup_days = 140 if daily else 14
    start = (today - timedelta(days=days)).isoformat()
    warmup_start = (today - timedelta(days=days + warmup_days)).isoformat()
    schedule = sessions("XNYS", warmup_start, today.isoformat())
    eligible = [
        day
        for day, (open_time, close_time) in schedule.items()
        if day >= start and (close_time if daily else open_time + BAR_INTERVAL) <= cutoff
    ]
    if not eligible:
        raise ValueError("No completed US regular-session bars are available for this period yet")
    last_session = max(eligible)
    open_time, close_time = schedule[last_session]
    expected_end = (
        close_time
        if close_time <= cutoff
        else open_time + ((cutoff - open_time) // BAR_INTERVAL) * BAR_INTERVAL
    )
    symbols = [symbol, "SPY"] if strategy["id"] == "residual_reversion" else [symbol]
    rows = sorted(
        (
            bar
            for bar in history(symbols, warmup_start, last_session, feed, cutoff)
            if bar.session in schedule
        ),
        key=lambda bar: (bar.symbol, bar.time),
    )
    latest = max(
        (
            instant(bar.time) + BAR_INTERVAL
            for bar in rows
            if bar.symbol == symbol and bar.session == last_session
        ),
        default=None,
    )
    if latest is None:
        raise ValueError(
            f"No {feed.upper()} bars for {symbol} in the latest session {last_session}"
        )
    if "SPY" in symbols and not any(bar.symbol == "SPY" for bar in rows):
        raise ValueError("No SPY benchmark bars were returned for residual reversion")
    quality = [
        "Recent exploratory window. Earlier bars are used for indicator warmup.",
        f"Latest {symbol} bar ended at {latest.isoformat()} UTC.",
    ]
    if daily:
        quality.append("Daily candles are aggregated from five-minute regular-session bars.")
    if feed == "iex":
        quality.append("IEX covers one exchange. Some intervals may have no reported trades.")
    else:
        quality.append("SIP uses a 16-minute delay for compatibility with basic Alpaca accounts.")
    if latest < expected_end:
        quality.append(
            f"No bar was returned for the final interval ending {expected_end.isoformat()} UTC."
        )
    metadata = {
        "name": f"Alpaca recent {symbol} · {period}",
        "source": "alpaca",
        "feed": feed,
        "timeframe": "1Day" if daily else "5Min",
        "currency": "USD",
        "adjustment": "split",
        "quality": quality,
        "recent": {
            "period": period,
            "as_of": as_of.isoformat(),
            "cutoff": cutoff.isoformat(),
            "latest_bar_end": latest.isoformat(),
            "expected_bar_end": expected_end.isoformat(),
            "warmup_start": warmup_start,
        },
    }
    spec = {
        "symbols": [symbol],
        "start": start,
        "end": last_session,
        "label": f"Recent {symbol} · {period} · {feed.upper()}",
        "split": "recent",
        "risk": {"capital": 1000},
    }
    return list(daily_bars(rows, schedule, cutoff)) if daily else rows, metadata, spec
