from datetime import date

from ..domain import Signal


def evaluate(ctx):
    if ctx.position:
        return None
    weekday = date.fromisoformat(ctx.bar.session).weekday()
    rule = ctx.params.get("weekday", "all")
    if rule == "friday" and weekday != 4:
        return None
    if rule == "weekdays" and weekday == 4:
        return None
    return Signal(1, "Scheduled close-to-open allocation", exit_mode="overnight")
