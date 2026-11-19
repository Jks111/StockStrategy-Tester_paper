from .common import purchases
from .insider_purchase import evaluate as purchase


def evaluate(ctx):
    if len({e.insider for e in purchases(ctx)}) < int(ctx.params.get("min_insiders", 2)):
        return None
    return purchase(ctx)
