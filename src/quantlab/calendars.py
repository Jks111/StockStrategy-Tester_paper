"""Versioned exchange schedules; scheduled sessions do not detect trading halts."""

from functools import lru_cache

import exchange_calendars as xcals


@lru_cache(maxsize=24)
def schedule(code, year):
    cal = xcals.get_calendar(code, start=f"{year}-01-01", end=f"{year}-12-31")
    return {
        str(day.date()): (row["open"].to_pydatetime(), row["close"].to_pydatetime())
        for day, row in cal.schedule.iterrows()
    }


def sessions(code, start, end):
    return {
        day: times
        for year in range(int(start[:4]), int(end[:4]) + 1)
        for day, times in schedule(code, year).items()
        if start <= day <= end
    }


VERSION = xcals.__version__
