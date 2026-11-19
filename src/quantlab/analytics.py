import math
import statistics
from datetime import date


def annual_breakdown(equity, trades, capital):
    years = {}
    previous = capital
    for point in equity:
        year = point["session"][:4]
        row = years.setdefault(
            year,
            {
                "year": year,
                "start_equity": previous,
                "end_equity": previous,
                "peak": previous,
                "max_drawdown": 0,
                "sessions": 0,
            },
        )
        row["end_equity"] = point["equity"]
        row["peak"] = max(row["peak"], point["equity"])
        row["max_drawdown"] = max(
            row["max_drawdown"], 1 - point["equity"] / row["peak"] if row["peak"] else 0
        )
        row["sessions"] += 1
        previous = point["equity"]
    result = []
    for year, row in years.items():
        subset = [t for t in trades if t["exit_time"][:4] == year]
        result.append(
            {
                "year": year,
                "net_return": row["end_equity"] / row["start_equity"] - 1
                if row["start_equity"] > 0
                else None,
                "max_drawdown": row["max_drawdown"],
                "trades": len(subset),
                "win_rate": sum(t["net_pnl"] > 0 for t in subset) / len(subset) if subset else None,
                "costs": sum(t["total_cost"] for t in subset),
                "sessions": row["sessions"],
            }
        )
    return result


def summarize(equity, trades, capital, benchmark, risk_free=0.0):
    values = [capital] + [p["equity"] for p in equity]
    returns = [b / a - 1 for a, b in zip(values, values[1:]) if a > 0]
    peak = capital
    drawdown = 0.0
    for v in values:
        peak = max(peak, v)
        if peak:
            drawdown = max(drawdown, 1 - v / peak)
    net = [t["net_pnl"] for t in trades]
    wins = sum(p for p in net if p > 0)
    losses = -sum(p for p in net if p < 0)
    years = (
        max(
            (
                date.fromisoformat(equity[-1]["session"]) - date.fromisoformat(equity[0]["session"])
            ).days
            / 365.25,
            1 / 365.25,
        )
        if equity
        else 0
    )
    total = values[-1] / capital - 1
    cagr = (values[-1] / capital) ** (1 / years) - 1 if years >= 1 and values[-1] > 0 else None
    sigma = statistics.stdev(returns) if len(returns) > 1 else 0
    sharpe = (
        (statistics.mean(returns) - risk_free / 252) / sigma * math.sqrt(252) if sigma else None
    )
    # Benchmark returns are aligned by session; intercept is historical realized alpha.
    bm = {p["session"]: p["close"] for p in benchmark}
    pairs = []
    for previous, current in zip(equity, equity[1:]):
        a, b = previous["session"], current["session"]
        if a in bm and b in bm and previous["equity"] > 0:
            pairs.append(
                (
                    bm[b] / bm[a] - 1 - risk_free / 252,
                    current["equity"] / previous["equity"] - 1 - risk_free / 252,
                )
            )
    beta = alpha = None
    if len(pairs) >= 30:
        mx = statistics.mean(x for x, y in pairs)
        my = statistics.mean(y for x, y in pairs)
        xx = sum((x - mx) ** 2 for x, y in pairs)
        if xx:
            beta = sum((x - mx) * (y - my) for x, y in pairs) / xx
            alpha = (my - beta * mx) * 252
    return {
        "net_return": total,
        "end_equity": values[-1],
        "max_drawdown": drawdown,
        "cagr": cagr,
        "sharpe": sharpe,
        "trades": len(trades),
        "win_rate": sum(p > 0 for p in net) / len(net) if net else None,
        "profit_factor": wins / losses if losses else None,
        "gross_profit": wins,
        "gross_loss": losses,
        "average_pnl": statistics.mean(net) if net else None,
        "average_hold_hours": statistics.mean(t["hold_hours"] for t in trades) if trades else None,
        "costs": sum(t["total_cost"] for t in trades),
        "beta": beta,
        "annualized_alpha": alpha,
        "capm_observations": len(pairs),
        "sessions": len(equity),
        "recovery_fraction": sum(t["was_negative"] and t["net_pnl"] > 0 for t in trades)
        / sum(t["was_negative"] for t in trades)
        if any(t["was_negative"] for t in trades)
        else None,
    }
