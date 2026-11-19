from .common import purchases
from .opening_ema import evaluate as opening


def evaluate(ctx):
    if ctx.position:
        return opening(ctx)
    return opening(ctx) if purchases(ctx) else None
