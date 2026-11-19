from datetime import timedelta
from zoneinfo import ZoneInfo

from ..domain import Signal, instant
from ..features import ema
from .common import entry


def evaluate(ctx):
    length = int(ctx.params.get("ema_length", 12))
    line = ema([b.close for b in ctx.history[-max(200, length * 5) :]], length)
    if line is None:
        return None
    if ctx.position:
        if (ctx.bar.close - line) * ctx.position.side <= 0:
            return Signal(0, "EMA momentum lost")
        return None
    local = (instant(ctx.bar.time) + timedelta(minutes=5)).astimezone(ZoneInfo("America/New_York"))
    if (local.hour, local.minute) != (9, 35):
        return None
    side = 1 if ctx.bar.close > line else -1 if ctx.bar.close < line else 0
    if not side or (side < 0 and not ctx.params.get("allow_short")):
        return None
    return entry(
        ctx,
        "Opening candle above EMA" if side > 0 else "Opening candle below EMA",
        side,
        "trail_session",
    )
