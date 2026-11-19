"""Structural checks, never a claim of independently verified price accuracy."""

from collections import Counter
from datetime import timedelta

from ..calendars import VERSION, sessions
from ..domain import instant
from ..storage import now


def audit(metadata, groups):
    calendar = sessions("XNYS", metadata["start"], metadata["end"])
    reports = []
    for symbol, bars in groups.items():
        seen = Counter(b.session for b in bars)
        missing = sorted(set(calendar) - set(seen))
        unexpected = sorted(set(seen) - set(calendar))
        boundaries = incomplete = 0
        by_day = {}
        for b in bars:
            by_day.setdefault(b.session, []).append(b)
            if b.session not in calendar:
                continue
            opening, closing = calendar[b.session]
            stamp = instant(b.time)
            if not opening <= stamp < closing or instant(b.session_end) != closing:
                boundaries += 1
        step = (
            None
            if metadata["timeframe"] == "1Day"
            else timedelta(minutes=int(metadata["timeframe"].replace("Min", "")))
        )
        for day, rows in by_day.items():
            if day not in calendar:
                continue
            opening, closing = calendar[day]
            expected = {opening}
            if step:
                expected = set()
                while opening < closing:
                    expected.add(opening)
                    opening += step
            if {instant(b.time) for b in rows} != expected:
                incomplete += 1
        reports.append(
            {
                "symbol": symbol,
                "bars": len(bars),
                "missing_sessions": len(missing),
                "unexpected_sessions": len(unexpected),
                "boundary_errors": boundaries,
                "incomplete_sessions": incomplete,
                "missing_examples": missing[:5],
                "unexpected_examples": unexpected[:5],
            }
        )
    issues = sum(
        sum(
            r[k]
            for k in (
                "missing_sessions",
                "unexpected_sessions",
                "boundary_errors",
                "incomplete_sessions",
            )
        )
        for r in reports
    )
    return {
        "created": now(),
        "dataset": metadata["id"],
        "dataset_hash": metadata["hash"],
        "calendar": "XNYS",
        "calendar_version": VERSION,
        "symbols": reports,
        "status": "synthetic"
        if metadata["source"] == "synthetic"
        else "review required"
        if issues
        else "structural checks passed",
        "issues": issues,
        "notes": [
            "OHLC, finite values and duplicate timestamps are checked on import.",
            "Missing bars may reflect listing dates, halts or no trades; investigate rather than forward-fill.",
            "Passing these checks does not verify prices, corporate actions, bid/ask spreads or a survivorship-free universe.",
            "US regular sessions only. Synthetic fixtures intentionally include simplified calendars.",
        ],
    }
