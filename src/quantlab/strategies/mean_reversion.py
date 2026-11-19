from ..domain import Signal
from ..features import zscore
from .common import entry


def evaluate(ctx):
    n = int(ctx.params.get("lookback", 20))
    if len(ctx.history) < n:
        return None
    z = zscore([b.close for b in ctx.history[-n:]])
    if ctx.position:
        return Signal(0, "Mean recovered") if z >= float(ctx.params.get("exit_z", 0)) else None
    return (
        entry(ctx, "Price below rolling mean")
        if z <= float(ctx.params.get("entry_z", -2))
        else None
    )
