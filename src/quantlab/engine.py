"""Deterministic OHLC event simulator; no broker calls are reachable here."""

import math
import sys
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .analytics import annual_breakdown, summarize
from .domain import Context, History, Position, Signal, instant
from .features import atr
from .storage import digest, now
from .strategies import definition, load

DEFAULT_RISK = {
    "capital": 1000,
    "max_positions": 4,
    "position_fraction": 0.2,
    "risk_fraction": 0.01,
    "spread_bps": 4,
    "slippage_bps": 2,
    "commission": 0,
    "borrow_apr": 0.03,
    "max_drawdown": 0.2,
    "risk_free": 0.0,
    "share_mode": "fractional",
}


def validate(spec, metadata):
    strategy = definition(spec.get("strategy", ""))
    if strategy["timeframe"] not in ("any", metadata["timeframe"]):
        raise ValueError(f"{strategy['name']} requires {strategy['timeframe']} data")
    symbols = spec.get("symbols") or metadata["symbols"]
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(s not in metadata["symbols"] for s in symbols)
    ):
        raise ValueError("Select symbols present in the dataset")
    params = {**strategy["defaults"], **spec.get("params", {})}
    if any(k not in strategy["defaults"] for k in params):
        raise ValueError("Unknown strategy parameter")
    for k, v in params.items():
        if k == "weekday":
            if v not in ("all", "friday", "weekdays"):
                raise ValueError("Invalid weekday filter")
        elif k == "allow_short":
            if not isinstance(v, bool):
                raise ValueError("allow_short must be true or false")
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError("Parameters must be finite numbers")
            if k in ("lookback", "ema_length", "hold_bars", "event_days", "min_insiders") and (
                int(v) != v or not 2 <= v <= 500
            ):
                raise ValueError(f"{k} must be an integer from 2 to 500")
            if k == "min_r2" and not 0 <= v <= 1:
                raise ValueError("R² must be between 0 and 1")
            if (
                k in ("atr_multiple", "target_multiple", "target_cash", "min_value")
                and not 0 < v <= 10000000
            ):
                raise ValueError(f"Invalid {k}")
            if k in ("entry_z", "exit_z") and not -10 <= v <= 10:
                raise ValueError("Z scores must be between -10 and 10")
    risk = {**DEFAULT_RISK, **spec.get("risk", {})}
    if any(k not in DEFAULT_RISK for k in risk):
        raise ValueError("Unknown risk setting")
    for k, v in risk.items():
        if k == "share_mode":
            if v not in ("whole", "fractional"):
                raise ValueError("Share mode must be whole or fractional")
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError("Risk settings must be finite numbers")
    if not 100 <= risk["capital"] <= 1e9:
        raise ValueError("Capital must be 100–1 billion")
    if int(risk["max_positions"]) != risk["max_positions"] or not 1 <= risk["max_positions"] <= 100:
        raise ValueError("Invalid position count")
    if (
        not 0 < risk["position_fraction"] <= 1
        or not 0 < risk["risk_fraction"] <= 0.1
        or not 0 < risk["max_drawdown"] <= 1
    ):
        raise ValueError("Invalid allocation, loss or drawdown limit")
    if any(not 0 <= risk[k] <= 1000 for k in ("spread_bps", "slippage_bps", "commission")):
        raise ValueError("Invalid transaction costs")
    if not 0 <= risk["borrow_apr"] <= 1 or not 0 <= risk["risk_free"] <= 0.3:
        raise ValueError("Invalid annual rate")
    start = spec.get("start") or metadata["start"]
    end = spec.get("end") or metadata["end"]
    from datetime import date

    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("Start must precede end")
    return {
        "dataset": metadata["id"],
        "strategy": strategy["id"],
        "symbols": sorted(set(symbols)),
        "params": params,
        "risk": risk,
        "start": start,
        "end": end,
        "label": str(spec.get("label", ""))[:120],
        "split": spec.get("split", "exploratory"),
    }


def backtest(spec, metadata, all_bars, events, progress=lambda value: None, liquidate_end=True):
    spec = validate(spec, metadata)
    risk = spec["risk"]
    capital = risk["capital"]
    bars = {s: all_bars[s] for s in spec["symbols"]}
    if not any(
        spec["start"] <= b.session <= spec["end"] for series in bars.values() for b in series
    ):
        raise ValueError("No bars in selected dates")
    if "residual" in spec["strategy"] and "SPY" not in all_bars:
        raise ValueError("Residual strategy requires SPY in the dataset")
    synthetic = metadata["source"] == "synthetic"
    events = [e for e in events if (e.source == "synthetic") == synthetic]
    symbol_events = {s: [e for e in events if e.symbol == s] for s in bars}
    if definition(spec["strategy"])["family"] in ("insider", "combined") and not any(
        e.available_at and e.code == "P" and e.symbol in bars for e in events
    ):
        raise ValueError(
            "No usable public code P purchases for these symbols. Unknown legacy availability is excluded."
        )
    interval = (
        timedelta(days=1)
        if metadata["timeframe"] == "1Day"
        else timedelta(minutes=int(metadata["timeframe"].replace("Min", "")))
    )

    def endtime(b):
        return (
            instant(b.session_end)
            if metadata["timeframe"] == "1Day"
            else min(instant(b.time) + interval, instant(b.session_end))
        )

    market = {b.session: b.close for b in all_bars.get("SPY", [])}
    params = {s: {**spec["params"], "_market": market} for s in bars}
    evaluate = load(spec["strategy"])
    schedule = []
    for symbol, series in bars.items():
        for index, b in enumerate(series):
            if spec["start"] <= b.session <= spec["end"]:
                schedule.extend(
                    [(instant(b.time), 1, symbol, index), (endtime(b), 0, symbol, index)]
                )
    schedule.sort()
    cash = capital
    positions = {}
    pending = {}
    prices = {}
    trades = []
    equity_map = {}
    last_trade = {}
    excursions = {}
    peak = capital
    halted = False
    rejected = 0
    entry_cost = sum([risk["spread_bps"] / 2, risk["slippage_bps"]]) / 10000
    overnight = spec["strategy"] in ("overnight", "overnight_weekend", "insider_overnight")
    current_time = schedule[0][0]

    def equity():
        accrued = sum(
            p.quantity
            * p.entry
            * risk["borrow_apr"]
            * max(0, (current_time - instant(p.entry_time)).total_seconds())
            / (365 * 86400)
            for p in positions.values()
            if p.side < 0
        )
        return (
            cash
            + sum(p.side * p.quantity * prices.get(s, p.entry) for s, p in positions.items())
            - accrued
        )

    def available():
        # Shorts reserve full initial notional plus proceeds. No portfolio leverage.
        return max(
            0, cash - sum(2 * p.quantity * p.entry for p in positions.values() if p.side < 0)
        )

    def enter(symbol, b, signal, price, timestamp, signal_time):
        nonlocal cash, rejected
        if halted or symbol in positions or len(positions) >= risk["max_positions"]:
            rejected += 1
            return
        if signal.side < 0 and not spec["params"].get("allow_short", False):
            rejected += 1
            return
        fill = price * (1 + signal.side * entry_cost)
        stop = (
            signal.stop_price
            if signal.stop_price is not None
            else fill - signal.side * signal.stop_distance
            if signal.stop_distance
            else None
        )
        distance = abs(fill - stop) if stop else 0
        if stop and (fill - stop) * signal.side <= 0:
            rejected += 1
            return
        allocation = min(
            max(0, equity()) * risk["position_fraction"],
            max(0, available() - risk["commission"]) / (1 + entry_cost),
        )
        qty = min(
            allocation / fill,
            max(0, equity()) * risk["risk_fraction"] / distance if distance else allocation / fill,
        )
        precision = 1 if risk["share_mode"] == "whole" else 1000000
        qty = math.floor(qty * precision) / precision
        if qty <= 0:
            rejected += 1
            return
        cash -= signal.side * qty * fill + risk["commission"]
        target = signal.target_price
        if signal.exit_mode == "cash_target":
            target = (
                fill + (float(spec["params"]["target_cash"]) + 2 * risk["commission"]) / qty
            ) / (1 - entry_cost)
        positions[symbol] = Position(
            symbol,
            signal.side,
            qty,
            fill,
            timestamp,
            risk["commission"],
            signal.reason,
            signal_time,
            stop,
            target,
            signal.exit_mode,
            signal.max_bars,
            peak=fill,
            trough=fill,
        )
        excursions[symbol] = {"mae": 0.0, "mfe": 0.0, "first_negative": None, "recovered_at": None}
        last_trade[symbol] = b.session

    def close(symbol, price, timestamp, reason):
        nonlocal cash
        p = positions.pop(symbol)
        ex = excursions.pop(symbol)
        fill = price * (1 - p.side * entry_cost)
        hours = max(0, (instant(timestamp) - instant(p.entry_time)).total_seconds() / 3600)
        borrow = p.quantity * p.entry * risk["borrow_apr"] * hours / (24 * 365) if p.side < 0 else 0
        cash += p.side * p.quantity * fill - risk["commission"] - borrow
        gross = p.side * p.quantity * (price - p.entry / (1 + p.side * entry_cost))
        net = p.side * p.quantity * (fill - p.entry) - p.entry_fee - risk["commission"] - borrow
        trades.append(
            {
                "symbol": symbol,
                "side": "long" if p.side > 0 else "short",
                "quantity": p.quantity,
                "signal_time": p.signal_time,
                "entry_time": p.entry_time,
                "exit_time": timestamp,
                "entry_price": p.entry,
                "exit_price": fill,
                "entry_reason": p.entry_reason,
                "exit_reason": reason,
                "gross_pnl": gross,
                "net_pnl": net,
                "total_cost": gross - net,
                "hold_hours": hours,
                "mae": ex["mae"],
                "mfe": ex["mfe"],
                "was_negative": ex["first_negative"] is not None,
                "recovery_hours": (
                    instant(ex["recovered_at"]) - instant(ex["first_negative"])
                ).total_seconds()
                / 3600
                if ex["recovered_at"]
                else None,
            }
        )

    for number, (timestamp, phase, symbol, index) in enumerate(schedule):
        current_time = timestamp
        b = bars[symbol][index]
        stamp = timestamp.isoformat()
        if phase == 1:
            prices[symbol] = b.open
            p = positions.get(symbol)
            if (
                p
                and p.exit_mode == "overnight"
                and b.session
                != instant(p.entry_time).astimezone(ZoneInfo("America/New_York")).date().isoformat()
            ):
                close(symbol, b.open, stamp, "Next session open")
            if symbol in pending:
                signal, signal_stamp = pending.pop(symbol)
                if signal.side == 0:
                    if symbol in positions:
                        close(symbol, b.open, stamp, signal.reason)
                else:
                    enter(symbol, b, signal, b.open, stamp, signal_stamp)
        else:
            prices[symbol] = b.close
            p = positions.get(symbol)
            if p:
                p.bars_held += 1
                ex = excursions[symbol]
                worst = p.side * p.quantity * ((b.low if p.side > 0 else b.high) - p.entry)
                best = p.side * p.quantity * ((b.high if p.side > 0 else b.low) - p.entry)
                ex["mae"] = min(ex["mae"], worst)
                ex["mfe"] = max(ex["mfe"], best)
                if worst < 0 and not ex["first_negative"]:
                    ex["first_negative"] = stamp
                stop_hit = p.stop is not None and (
                    b.low <= p.stop if p.side > 0 else b.high >= p.stop
                )
                target_hit = p.target is not None and (
                    b.high >= p.target if p.side > 0 else b.low <= p.target
                )
                # A close after an intrabar stop/target cannot establish recovery while held.
                if (
                    not stop_hit
                    and not target_hit
                    and ex["first_negative"]
                    and not ex["recovered_at"]
                    and p.side * (b.close - p.entry) >= 0
                ):
                    ex["recovered_at"] = stamp
                if stop_hit:
                    price = min(b.open, p.stop) if p.side > 0 else max(b.open, p.stop)
                    close(
                        symbol,
                        price,
                        instant(b.time).isoformat() if (b.open - p.stop) * p.side < 0 else stamp,
                        "Stop (adverse first if ambiguous)" if target_hit else "Protective stop",
                    )
                elif target_hit:
                    price = max(b.open, p.target) if p.side > 0 else min(b.open, p.target)
                    close(symbol, price, stamp, "Profit target")
                elif p.exit_mode in ("session", "trail_session") and timestamp >= instant(
                    b.session_end
                ) - timedelta(minutes=5):
                    close(symbol, b.close, stamp, "Scheduled session exit")
                elif p.max_bars and p.bars_held >= p.max_bars:
                    pending[symbol] = (Signal(0, "Maximum holding bars"), stamp)
                if symbol in positions and p.exit_mode.startswith("trail"):
                    distance = (
                        atr(bars[symbol][max(0, index - 15) : index + 1]) or b.close * 0.02
                    ) * float(spec["params"].get("atr_multiple", 3))
                    p.peak = max(p.peak, b.high)
                    p.trough = min(p.trough, b.low)
                    new = p.peak - distance if p.side > 0 else p.trough + distance
                    p.stop = (
                        max(p.stop, new)
                        if p.side > 0 and p.stop is not None
                        else min(p.stop, new)
                        if p.side < 0 and p.stop is not None
                        else new
                    )
            current = equity()
            peak = max(peak, current)
            if peak and 1 - current / peak >= risk["max_drawdown"]:
                halted = True
            ctx = Context(
                History(bars[symbol], index + 1),
                index,
                symbol_events[symbol],
                params[symbol],
                positions.get(symbol),
                stamp,
            )
            if overnight:
                # Eligibility uses only information available five minutes before scheduled close.
                ctx.decision_time = (timestamp - timedelta(minutes=5)).isoformat()
                signal = evaluate(ctx)
                if signal and not halted:
                    enter(symbol, b, signal, b.close, stamp, ctx.decision_time)
            elif symbol not in pending:
                signal = evaluate(ctx)
                intraday_once = spec["strategy"] in (
                    "opening_ema",
                    "opening_range",
                    "insider_opening",
                )
                if signal and (
                    signal.side == 0
                    or not halted
                    and (not intraday_once or last_trade.get(symbol) != b.session)
                ):
                    pending[symbol] = (signal, stamp)
            equity_map[b.session] = {
                "session": b.session,
                "equity": equity(),
                "positions": len(positions),
                "cash": cash,
                "exposure": {s: p.quantity * prices.get(s, p.entry) for s, p in positions.items()},
            }
        if number % 1000 == 0:
            progress(number / len(schedule))
    # Explicit terminal liquidation, flagged in each trade; never silently drop losers.
    for symbol in list(positions) if liquidate_end else []:
        last = next(b for b in reversed(bars[symbol]) if spec["start"] <= b.session <= spec["end"])
        close(symbol, last.close, endtime(last).isoformat(), "End of test liquidation")
    equity_points = [equity_map[k] for k in sorted(equity_map)]
    if equity_points:
        equity_points[-1].update(
            equity=equity(),
            cash=cash,
            positions=len(positions),
            exposure={s: p.quantity * prices.get(s, p.entry) for s, p in positions.items()},
        )
    benchmark = []
    for b in all_bars.get("SPY", []):
        if spec["start"] <= b.session <= spec["end"]:
            benchmark.append({"session": b.session, "close": b.close})
    benchmark = list({p["session"]: p for p in benchmark}.values())
    warnings = list(metadata.get("quality", [])) + [
        "OHLC fills are estimates. Stops precede targets when both touch; no queue, auction, latency or volume-fill model.",
        "Split-adjusted data; dividends are not reinvested unless already included by your dataset. Results are before tax.",
        "Selected-symbol universe; no automatic historical membership or delisted-security coverage.",
        "Drawdown circuit breaker blocks new entries; gaps and open positions can exceed the limit.",
    ]
    if overnight:
        warnings.append(
            "Daily official-close fills are idealized scheduled-auction proxies. Actual auction eligibility and costs need intraday/quote validation."
        )
    if spec["params"].get("allow_short"):
        warnings.append(
            "Short availability is assumed, with fixed borrow APR; locate/recall restrictions are not simulated."
        )
    warnings.append(
        "MAE/MFE are full-bar excursion bounds, not ordered tick paths; exit-bar extremes may occur after a simulated exit. Recovery uses completed-bar marks while held."
    )
    warnings.append(
        "Sample-dependent historical alpha and Sharpe are descriptive; hundreds of trials require multiple-testing controls."
    )
    strategy_files = list(Path(__file__).parent.glob("*.py")) + list(
        (Path(__file__).parent / "strategies").glob("*.py")
    )
    code_hash = digest({p.name: p.read_text(encoding="utf8") for p in strategy_files})
    metrics = summarize(equity_points, trades, capital, benchmark, risk["risk_free"])
    metrics.update(rejected_entries=rejected, drawdown_halt=halted)
    return {
        "spec": spec,
        "metrics": metrics,
        "equity": equity_points,
        "trades": trades,
        "benchmark": benchmark,
        "warnings": warnings,
        "open_positions": [asdict(p) for p in positions.values()],
        "annual": annual_breakdown(equity_points, trades, capital),
        "manifest": {
            "created": now(),
            "engine": "ohlc-event-v1",
            "code_hash": code_hash,
            "python": sys.version.split()[0],
            "dataset": metadata,
            "spec": spec,
            "events_hash": digest([asdict(e) for e in events]),
            "public_events": sum(bool(e.available_at) for e in events),
            "execution": "signals after completed bars; next open; explicit scheduled close exceptions",
            "warnings": warnings,
        },
    }
