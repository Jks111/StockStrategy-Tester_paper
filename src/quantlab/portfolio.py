"""Fixed strategy allocations within one total budget; no cross-sleeve borrowing."""

from .analytics import annual_breakdown, summarize
from .engine import backtest
from .storage import digest, now


def backtest_portfolio(spec, metadata, bars, events, progress=lambda value: None):
    results = []
    components = spec["components"]
    for i, component in enumerate(components):
        result = backtest(
            component["spec"], metadata, bars, events, lambda p: progress((i + p) / len(components))
        )
        results.append(result)
    days = sorted({p["session"] for r in results for p in r["equity"]})
    lookups = [{p["session"]: p for p in r["equity"]} for r in results]
    latest = [
        {
            "equity": c["spec"]["risk"]["capital"],
            "cash": c["spec"]["risk"]["capital"],
            "positions": 0,
            "exposure": {},
        }
        for c in components
    ]
    equity = []
    peak_concentration = 0
    for day in days:
        for i, lookup in enumerate(lookups):
            latest[i] = lookup.get(day, latest[i])
        point = {
            "session": day,
            **{k: sum(p[k] for p in latest) for k in ("equity", "cash", "positions")},
        }
        exposure = {}
        for p in latest:
            for symbol, value in p.get("exposure", {}).items():
                exposure[symbol] = exposure.get(symbol, 0) + value
        point["exposure"] = exposure
        if point["equity"] > 0 and exposure:
            peak_concentration = max(peak_concentration, max(exposure.values()) / point["equity"])
        equity.append(point)
    trades = sorted(
        [
            {**t, "allocation": i + 1, "strategy": c["spec"]["strategy"]}
            for i, (c, r) in enumerate(zip(components, results))
            for t in r["trades"]
        ],
        key=lambda t: t["exit_time"],
    )
    capital = spec["risk"]["capital"]
    benchmark = results[0]["benchmark"]
    metrics = summarize(equity, trades, capital, benchmark, spec["risk"]["risk_free"])
    metrics.update(
        peak_symbol_exposure=peak_concentration,
        rejected_entries=sum(r["metrics"]["rejected_entries"] for r in results),
    )
    warnings = list(dict.fromkeys(w for r in results for w in r["warnings"])) + [
        "One total starting balance, divided into fixed allocations. Idle cash is not lent between strategies and allocations are not rebalanced.",
        "Position limits and drawdown halts apply per allocation; there is no global liquidation rule. Opposing positions are not netted and incur separate costs.",
        "Exposure is measured at session closes; intraday concentration may be higher.",
    ]
    return {
        "spec": spec,
        "metrics": metrics,
        "equity": equity,
        "trades": trades,
        "benchmark": benchmark,
        "annual": annual_breakdown(equity, trades, capital),
        "warnings": warnings,
        "open_positions": [],
        "components": [
            {"weight": c["weight"], "spec": r["spec"], "metrics": r["metrics"]}
            for c, r in zip(components, results)
        ],
        "manifest": {
            "created": now(),
            "engine": "fixed-allocation-portfolio-v1",
            "dataset": metadata,
            "spec": spec,
            "warnings": warnings,
            "code_hash": results[0]["manifest"]["code_hash"],
            "events_hash": results[0]["manifest"]["events_hash"],
            "components_hash": digest([r["manifest"] for r in results]),
        },
    }
