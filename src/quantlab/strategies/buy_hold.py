from ..domain import Signal


def evaluate(ctx):
    return None if ctx.position else Signal(1, "Initial allocation", exit_mode="hold")
