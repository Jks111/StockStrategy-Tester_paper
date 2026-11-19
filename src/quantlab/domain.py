from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include a UTC offset")
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Bar:
    symbol: str
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: str
    session_end: str


@dataclass(frozen=True)
class InsiderEvent:
    event_id: str
    symbol: str
    insider: str
    transaction_date: str
    available_at: str | None
    observed_at: str
    code: str
    shares: float
    price: float
    source: str
    role: str = ""
    accession: str = ""
    ownership: str = ""
    amended: bool = False
    snapshot_date: str = ""


@dataclass(frozen=True)
class Signal:
    side: int
    reason: str
    stop_distance: float = 0.0
    target_price: float | None = None
    stop_price: float | None = None
    exit_mode: str = "hold"
    max_bars: int = 0


@dataclass
class Position:
    symbol: str
    side: int
    quantity: float
    entry: float
    entry_time: str
    entry_fee: float
    entry_reason: str
    signal_time: str
    stop: float | None
    target: float | None
    exit_mode: str
    max_bars: int
    bars_held: int = 0
    peak: float = 0.0
    trough: float = float("inf")


@dataclass
class Context:
    bars: Sequence[Bar]
    index: int
    events: list[InsiderEvent]
    params: dict[str, Any]
    position: Position | None = None
    decision_time: str | None = None

    @property
    def bar(self):
        return self.bars[self.index]

    @property
    def history(self):
        return History(self.bars, self.index + 1)

    def public_events(self, days=30):
        from datetime import timedelta

        now = instant(self.decision_time or self.bar.time)
        return [
            e
            for e in self.events
            if e.symbol == self.bar.symbol
            and e.code == "P"
            and not e.amended
            and e.available_at
            and now - timedelta(days=days) <= instant(e.available_at) <= now
        ]


def record(obj):
    return asdict(obj)


class History(Sequence):
    """Read-only prefix view: bounded slices copy only the requested lookback."""

    def __init__(self, bars, stop):
        self._bars = bars
        self._stop = min(len(bars), stop)

    def __len__(self):
        return self._stop

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(self._stop)
            if step < 0:
                return [self._bars[i] for i in range(start, stop, step)]
            return self._bars[start:stop:step]
        if key < 0:
            key += self._stop
        if not 0 <= key < self._stop:
            raise IndexError("Bar has not completed at this decision")
        return self._bars[key]
