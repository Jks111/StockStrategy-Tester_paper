"""A cumulative return-residual prototype, not a replication of a pairs paper."""

import math
import statistics

from ..domain import Signal
from ..features import zscore
from .common import entry


def evaluate(ctx):
    market = ctx.params.get("_market", {})
    n = int(ctx.params.get("lookback", 60))
    bars = ctx.history[-n - 1 :]
    if len(bars) < n + 1 or any(b.session not in market for b in bars):
        return None
    if ctx.bar.symbol == "SPY":
        return None
    x = [math.log(market[b.session] / market[a.session]) for a, b in zip(bars, bars[1:])]
    y = [math.log(b.close / a.close) for a, b in zip(bars, bars[1:])]
    mx, my = statistics.mean(x), statistics.mean(y)
    variance = sum((v - mx) ** 2 for v in x)
    beta = sum((u - mx) * (v - my) for u, v in zip(x, y)) / variance if variance else 0
    residual = [v - my - beta * (u - mx) for u, v in zip(x, y)]
    path = []
    total = 0
    for r in residual:
        total += r
        path.append(total)
    z = zscore(path)
    if ctx.position:
        return Signal(0, "Residual recovered") if z >= 0 else None
    return (
        entry(ctx, "Negative market residual deviation")
        if z <= float(ctx.params.get("entry_z", -2))
        else None
    )
