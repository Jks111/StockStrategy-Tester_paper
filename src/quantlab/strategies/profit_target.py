from .common import entry


def evaluate(ctx):
    return (
        None
        if ctx.position
        else entry(ctx, "Fixed-cash target experiment", exit_mode="cash_target")
    )
