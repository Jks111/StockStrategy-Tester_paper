from .common import purchases
from .mean_reversion import evaluate as reversion


def evaluate(ctx):
    if ctx.position:
        return reversion(ctx)
    return reversion(ctx) if purchases(ctx) else None
