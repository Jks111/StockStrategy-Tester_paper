from zoneinfo import ZoneInfo

from ..domain import instant
from ..features import atr
from .common import entry


def evaluate(ctx):
    if ctx.position:
        return None
    local = instant(ctx.decision_time).astimezone(ZoneInfo("America/New_York"))
    if not ((9, 45) <= (local.hour, local.minute) <= (15, 0)):
        return None
    opening = [
        b
        for b in ctx.history[-78:]
        if b.session == ctx.bar.session
        and (9, 30)
        <= (
            instant(b.time).astimezone(ZoneInfo("America/New_York")).hour,
            instant(b.time).astimezone(ZoneInfo("America/New_York")).minute,
        )
        < (9, 45)
    ]
    if len(opening) != 3:
        return None
    hi, lo = max(b.high for b in opening), min(b.low for b in opening)
    side = 1 if ctx.bar.close > hi else -1 if ctx.bar.close < lo else 0
    if not side or (side < 0 and not ctx.params.get("allow_short")):
        return None
    pad = (atr(ctx.history) or hi - lo) * float(ctx.params.get("atr_multiple", 0.25))
    target = (hi if side > 0 else lo) + side * float(ctx.params.get("target_multiple", 2)) * (
        hi - lo
    )
    stop = lo - pad if side > 0 else hi + pad
    return entry(ctx, "Opening range breakout", side, "session", target, stop)
