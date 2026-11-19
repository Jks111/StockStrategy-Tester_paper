from ..domain import Signal
from ..features import atr


def entry(ctx, reason, side=1, exit_mode="hold", target=None, stop=None):
    distance = (atr(ctx.history) or ctx.bar.close * 0.02) * float(ctx.params.get("atr_multiple", 3))
    return Signal(
        side, reason, distance, target, stop, exit_mode, int(ctx.params.get("hold_bars", 0))
    )


def purchases(ctx):
    threshold = float(ctx.params.get("min_value", 10000))
    return [
        e
        for e in ctx.public_events(int(ctx.params.get("event_days", 10)))
        if e.shares * e.price >= threshold
    ]
