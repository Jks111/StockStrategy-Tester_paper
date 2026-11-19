from .common import entry, purchases


def evaluate(ctx):
    if ctx.position:
        return None
    events = purchases(ctx)
    if not events:
        return None
    fresh = max(e.available_at for e in events)
    if ctx.params.get("_last_event") == fresh:
        return None
    ctx.params["_last_event"] = fresh
    return entry(ctx, "Public non-amended insider purchase")
