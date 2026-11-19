import math
import statistics


def ema(values, length=12):
    if len(values) < length:
        return None
    result = values[0]
    alpha = 2 / (length + 1)
    for value in values[1:]:
        result += alpha * (value - result)
    return result


def atr(bars, length=14):
    if len(bars) < length + 1:
        return None
    recent = bars[-length - 1 :]
    return statistics.mean(
        max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close))
        for p, b in zip(recent, recent[1:])
    )


def regression(values):
    n = len(values)
    if n < 3:
        return 0.0, 0.0
    xm, ym = (n - 1) / 2, statistics.mean(values)
    xx = sum((i - xm) ** 2 for i in range(n))
    slope = sum((i - xm) * (y - ym) for i, y in enumerate(values)) / xx
    total = sum((y - ym) ** 2 for y in values)
    error = sum((y - (ym + slope * (i - xm))) ** 2 for i, y in enumerate(values))
    return slope, max(0, 1 - error / total) if total else 0


def zscore(values):
    if len(values) < 3:
        return 0.0
    std = statistics.pstdev(values)
    return (values[-1] - statistics.mean(values)) / std if std else 0.0


def log_trend(bars, length):
    return regression([math.log(b.close) for b in bars[-length:]])
