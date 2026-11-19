from .common import purchases
from .overnight import evaluate as overnight


def evaluate(ctx):
    return overnight(ctx) if purchases(ctx) else None
