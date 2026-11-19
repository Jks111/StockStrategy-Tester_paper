from .common import purchases
from .regression_trend import evaluate as trend


def evaluate(ctx):
    if ctx.position:
        return trend(ctx)
    return trend(ctx) if purchases(ctx) else None
