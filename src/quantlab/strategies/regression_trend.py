from ..domain import Signal
from ..features import log_trend
from .common import entry


def evaluate(ctx):
    length = int(ctx.params.get("lookback", 40))
    if len(ctx.history) < length:
        return None
    slope, r2 = log_trend(ctx.history, length)
    if ctx.position:
        return Signal(0, "Regression slope lost") if slope <= 0 else None
    return (
        entry(ctx, "Positive log regression", exit_mode="trail")
        if slope > 0 and r2 >= float(ctx.params.get("min_r2", 0.6))
        else None
    )
