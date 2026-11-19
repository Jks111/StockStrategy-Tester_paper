"""Regular cash-equity hours, not a holiday calendar or live market status."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .calendars import VERSION, sessions

MARKETS = (
    (
        "Helsinki",
        "Nasdaq Helsinki",
        "Europe/Helsinki",
        "10:00",
        "18:30",
        "https://www.nasdaq.com/european-market-activity/trading-hours",
    ),
    (
        "USA",
        "NYSE / Nasdaq",
        "America/New_York",
        "09:30",
        "16:00",
        "https://www.nyse.com/trade/trading-information",
    ),
    (
        "UK",
        "London Stock Exchange",
        "Europe/London",
        "08:00",
        "16:30",
        "https://www.lseg.com/en/media-centre/press-releases/2026/london-stock-exchange-to-launch-lse-24",
    ),
    (
        "EU · Germany",
        "Xetra core session",
        "Europe/Berlin",
        "09:00",
        "17:30",
        "https://www.xetra.com/xetra-en/trading/trading-calendar-and-trading-hours",
    ),
)


def market_hours(now=None):
    helsinki = ZoneInfo("Europe/Helsinki")
    now = now or datetime.now(helsinki)
    if now.tzinfo is None:
        raise ValueError("Market hours require a timezone-aware timestamp")
    day = now.astimezone(helsinki).date()
    rows = []
    for code, (name, exchange, zone, opening, closing, source) in zip(
        ("XHEL", "XNYS", "XLON", "XETR"), MARKETS
    ):
        local = ZoneInfo(zone)
        converted = [
            datetime.combine(day, time.fromisoformat(value), local)
            .astimezone(helsinki)
            .strftime("%H:%M")
            for value in (opening, closing)
        ]
        calendar = sessions(code, day.isoformat(), (day + timedelta(days=15)).isoformat())
        today = calendar.get(day.isoformat())
        status = "Closed · holiday / weekend"
        today_hours = "No session today"
        early = False
        if today:
            opened, closed = today
            today_hours = "–".join(t.astimezone(helsinki).strftime("%H:%M") for t in today)
            early = closed.astimezone(local).strftime("%H:%M") < closing
            status = (
                "Scheduled open"
                if opened <= now < closed
                else "Before open"
                if now < opened
                else "Closed for today"
            )
        next_open = next((times[0] for times in calendar.values() if times[0] > now), None)
        rows.append(
            {
                "name": name,
                "exchange": exchange,
                "timezone": zone,
                "local": f"{opening}–{closing}",
                "helsinki": "–".join(converted),
                "source": source,
                "status": status,
                "today": today_hours,
                "early_close": early,
                "next_open": next_open.astimezone(helsinki).strftime("%a %d %b %H:%M")
                if next_open
                else None,
            }
        )
    return {"date": day.isoformat(), "markets": rows, "calendar_version": VERSION}
