"""Frozen SPY timing experiment. Historical data in, simulated session returns out."""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import sklearn
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression

from .calendars import sessions
from .data.providers import Alpaca
from .domain import Bar, instant
from .engine import DEFAULT_RISK
from .storage import digest
from .strategies.spy_timing import FILL_FEE, break_even, features

START, END = "2017-01-01", "2025-12-31"
SNAPSHOT_NAME = "SPY open-to-close research 2017-2025"
FOLDS = [(str(year), str(year)) for year in range(2020, 2024)] + [("2024", "2025")]
FEATURES = ["return_1d", "return_5d", "close_to_sma20", "volatility_20d", "relative_volume_20d"]
ARMS = ("every_open", "linear", "forest")
FOREST = dict(n_estimators=200, max_depth=4, min_samples_leaf=25, max_features=0.8, random_state=42)
CAPITAL = float(DEFAULT_RISK["capital"])
ALLOCATION = DEFAULT_RISK["position_fraction"]
SIDE_BPS = DEFAULT_RISK["spread_bps"] / 2 + DEFAULT_RISK["slippage_bps"]
FEE = FILL_FEE


@dataclass(frozen=True)
class Sample:
    bar: Bar
    feature_session: str
    features: tuple

    @property
    def target(self):
        return self.bar.close / self.bar.open - 1


def samples(bars):
    rows = []
    for i in range(21, len(bars)):
        history = bars[i - 21 : i]
        rows.append(Sample(bars[i], history[-1].session, features(history)))
    return rows


def forecast(rows):
    folds, predictions = [], []
    for first, last in FOLDS:
        train = [r for r in rows if r.bar.session[:4] < first]
        test = [r for r in rows if first <= r.bar.session[:4] <= last]
        x = [r.features for r in train]
        y = [r.target for r in train]
        test_x = [r.features for r in test]
        linear = LinearRegression().fit(x, y).predict(test_x)
        forest = RandomForestRegressor(**FOREST, n_jobs=1).fit(x, y).predict(test_x)
        fold = {
            "train_start": train[0].bar.session,
            "train_end": train[-1].bar.session,
            "training_sessions": len(train),
            "start": test[0].bar.session,
            "end": test[-1].bar.session,
            "test_sessions": len(test),
        }
        folds.append(fold)
        for row, lr, rf in zip(test, linear, forest, strict=True):
            predictions.append(
                {
                    "session": row.bar.session,
                    "feature_session": row.feature_session,
                    "train_end": fold["train_end"],
                    "open": row.bar.open,
                    "close": row.bar.close,
                    "open_time": row.bar.time,
                    "close_time": row.bar.session_end,
                    "linear": float(lr),
                    "forest": float(rf),
                }
            )
    return folds, predictions


def quantity(budget, entry):
    return math.floor(budget / entry * 1_000_000) / 1_000_000


def simulate(days, arm, side_bps=SIDE_BPS, fixed_decisions=None):
    side = side_bps / 10000
    equity = CAPITAL
    balances, trades, decisions, daily_returns = [equity], [], [], []
    for i, day in enumerate(days):
        before = equity
        budget = equity * ALLOCATION
        # Set the decision and dollar budget before observing the opening price.
        hurdle = break_even(budget, side, FEE)
        active = (
            fixed_decisions[i]
            if fixed_decisions is not None
            else arm == "every_open" or day[arm] > hurdle
        )
        decisions.append(active)
        if active:
            entry = day["open"] * (1 + side)
            exit_price = day["close"] * (1 - side)
            qty = quantity(budget, entry)
            gross = qty * (day["close"] - day["open"])
            net = qty * (exit_price - entry) - 2 * FEE
            equity += net
            hours = (instant(day["close_time"]) - instant(day["open_time"])).total_seconds() / 3600
            trades.append(
                {
                    "session": day["session"],
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "quantity": qty,
                    "net_pnl": net,
                    "total_cost": gross - net,
                    "hold_hours": hours,
                }
            )
        balances.append(equity)
        daily_returns.append(equity / before - 1)
    stats = {
        "sessions": len(days),
        "trades": len(trades),
        "end_equity": equity,
        "net_return": equity / CAPITAL - 1,
        "max_drawdown": float(np.max(1 - balances / np.maximum.accumulate(balances))),
        "costs": sum(t["total_cost"] for t in trades),
        "win_rate": sum(t["net_pnl"] > 0 for t in trades) / len(trades) if trades else None,
        "average_hold_hours": (
            sum(t["hold_hours"] for t in trades) / len(trades) if trades else None
        ),
    }
    stats.update(net_pnl=equity - CAPITAL, participation=len(trades) / len(days), open_positions=0)
    return stats, decisions, np.array(daily_returns), trades


def paired_checks(model, baseline):
    # Paired 20-session circular blocks preserve local serial dependence and flat days.
    rng = np.random.default_rng(42)
    n = len(model)
    starts = rng.integers(n, size=(2000, math.ceil(n / 20), 1))
    indices = ((starts + np.arange(20)) % n).reshape(2000, -1)[:, :n]

    def advantage(indices):
        return CAPITAL * (
            np.prod(1 + model[indices], axis=-1) - np.prod(1 + baseline[indices], axis=-1)
        )

    keep = np.argsort(np.log1p(model) - np.log1p(baseline))[:-5]
    return {
        "advantage_dollars": float(advantage(np.arange(n))),
        "block_bootstrap_95pct_dollars": np.quantile(advantage(indices), [0.025, 0.975]).tolist(),
        "advantage_without_best_5_relative_sessions": float(advantage(keep)),
    }


def random_timing(days, decisions, actual_pnl):
    """Descriptive timing control: shuffle entry dates within each calendar year."""
    rng = np.random.default_rng(43)
    years = np.array([d["session"][:4] for d in days])
    masks = np.empty((2000, len(days)), dtype=bool)
    for year in np.unique(years):
        mask = years == year
        observed = np.array(decisions)[mask]
        masks[:, mask] = rng.permuted(np.broadcast_to(observed, (2000, len(observed))), axis=1)
    balances = np.full(len(masks), CAPITAL)
    for i, day in enumerate(days):
        entry = day["open"] * (1 + SIDE_BPS / 10000)
        exit_price = day["close"] * (1 - SIDE_BPS / 10000)
        qty = np.floor(balances * ALLOCATION / entry * 1e6) / 1e6
        balances += masks[:, i] * (qty * (exit_price - entry) - 2 * FEE)
    pnl = balances - CAPITAL
    return {
        "median_net_pnl": float(np.median(pnl)),
        "central_95pct_net_pnl": np.quantile(pnl, [0.025, 0.975]).tolist(),
        "fraction_matching_or_beating_model": float(np.mean(pnl >= actual_pnl)),
    }


def evaluate(days, *, audit=False):
    stats, decisions, daily, ledger = {}, {}, {}, {}
    for arm in ARMS:
        stats[arm], decisions[arm], daily[arm], trades = simulate(days, arm)
        if audit:
            ledger[arm] = trades
    checks = {arm: paired_checks(daily[arm], daily["every_open"]) for arm in ARMS[1:]}
    for arm, check in checks.items():
        check["same_trade_count_random_dates"] = random_timing(
            days, decisions[arm], stats[arm]["net_pnl"]
        )
        active_days = np.flatnonzero(decisions[arm])
        best = active_days[np.argsort(daily[arm][active_days])[-5:]]
        reduced = np.array(decisions[arm])
        reduced[best] = False
        check["net_pnl_without_own_best_five_trades"] = simulate(
            days, arm, fixed_decisions=reduced
        )[0]["net_pnl"]
    sensitivity = {
        str(bps): {arm: simulate(days, arm, bps, decisions[arm])[0]["net_pnl"] for arm in ARMS}
        for bps in (1.0, 2 * SIDE_BPS)
    }
    # Match initial allocation; price-only context excludes distributions on overnight holdings.
    side = SIDE_BPS / 10000
    entry = days[0]["open"] * (1 + side)
    hold_pnl = quantity(CAPITAL * ALLOCATION, entry) * (days[-1]["close"] * (1 - side) - entry)
    result = {
        "start": days[0]["session"],
        "end": days[-1]["session"],
        "arms": stats,
        "paired": checks,
        "cost_stress_net_pnl_fixed_signals": sensitivity,
        "buy_hold_price_only_net_pnl": hold_pnl - 2 * FEE,
        "cash_net_pnl": 0,
    }
    if audit:
        result["trades"] = ledger
    return result


def check_snapshot(bars, meta):
    if (meta["source"], meta.get("feed"), meta["timeframe"], meta.get("adjustment")) != (
        "alpaca",
        "sip",
        "1Day",
        "split",
    ):
        raise ValueError("Use real, split-adjusted Alpaca SIP daily bars")
    calendar = sessions("XNYS", START, END)
    if [b.session for b in bars] != sorted(calendar):
        raise ValueError("SPY dates must cover every 2017–2025 exchange session exactly once")
    for b in bars:
        if b.volume <= 0 or (instant(b.time), instant(b.session_end)) != calendar[b.session]:
            raise ValueError(f"Invalid volume or session times on {b.session}")


def latest_report_path(store):
    reports = (store.root / "experiments" / "spy-open-close").glob("*/report.json")
    return max(reports, key=lambda p: p.stat().st_mtime_ns, default=None)


def run_dashboard(store):
    saved = latest_report_path(store)
    snapshots = store.datasets()
    if saved and any(d["id"] == saved.parent.name for d in snapshots):
        dataset_id = saved.parent.name
    else:
        dataset_id = next((d["id"] for d in snapshots if d.get("name") == SNAPSHOT_NAME
                           and d["source"] == "alpaca" and d.get("feed") == "sip"
                           and d["timeframe"] == "1Day" and d.get("adjustment") == "split"), None)
    return run(store, dataset_id)


def dashboard_report(store):
    path = latest_report_path(store)
    if path is None:
        return None
    report = json.loads(path.read_text(encoding="utf8"))
    return {
        "dataset": report["dataset"]["id"],
        "capital": report["capital"], "allocation": report["allocation"],
        "per_side_bps": report["per_side_bps"], "fee_per_fill": report["fee_per_fill"],
        "periods": {name: {k: p[k] for k in ("start", "end", "arms")}
                    for name, p in report["periods"].items()},
    }


def run(store, dataset_id):
    if dataset_id is None:
        bars = Alpaca().history(["SPY"], START, END, "1Day", "sip")
        meta = dict(
            name=SNAPSHOT_NAME,
            source="alpaca",
            feed="sip",
            timeframe="1Day",
            adjustment="split",
        )
        check_snapshot(bars, meta)
        meta = store.save_bars(bars, meta)
    else:
        meta = store.dataset(dataset_id)
        bars = [b for b in store.load_bars(dataset_id).get("SPY", []) if START <= b.session <= END]
        check_snapshot(bars, meta)
    folds, days = forecast(samples(bars))
    periods = {
        "walk_forward": evaluate([d for d in days if d["session"][:4] < "2024"]),
        "holdout": evaluate([d for d in days if d["session"][:4] >= "2024"]),
        "all_out_of_sample": evaluate(days, audit=True),
    }
    annual = {
        year: {
            arm: simulate([d for d in days if d["session"].startswith(year)], arm)[0]
            for arm in ARMS
        }
        for year in sorted({d["session"][:4] for d in days})
    }
    report = {
        "dataset": meta,
        "protocol": "spy-open-close-v1",
        "features": FEATURES,
        "forest": FOREST,
        "linear": "ordinary least squares with intercept",
        "folds": folds,
        "capital": CAPITAL,
        "allocation": ALLOCATION,
        "per_side_bps": SIDE_BPS,
        "fee_per_fill": FEE,
        "fee_method": "flat simulation allowance",
        "period_capital": "Each reported period and calendar year starts at the same initial capital.",
        "timing": "Finalized prior-day bars; next daily open and same-day close are fill proxies.",
        "sklearn": sklearn.__version__,
        "numpy": np.__version__,
        "experiment_code_hash": digest(Path(__file__).read_text(encoding="utf8")),
        "periods": periods,
        "annual": annual,
        "predictions": days,
    }
    folder = store.root / "experiments" / "spy-open-close" / meta["id"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "report.json"
    temp = NamedTemporaryFile(mode="w", dir=folder, delete=False, encoding="utf8")
    try:
        with temp:
            json.dump(report, temp, allow_nan=False, indent=2)
        os.replace(temp.name, path)
    finally:
        Path(temp.name).unlink(missing_ok=True)
    lines = [f"SPY open-to-close simulation | {len(bars)} sessions | dataset {meta['id']}"]
    for label, period in periods.items():
        lines.append(f"{label} ({period['start']} to {period['end']})")
        for arm, m in period["arms"].items():
            lines.append(
                f"  {arm:12} net {m['net_pnl']:+.2f} USD, {m['trades']} trades, "
                f"drawdown {m['max_drawdown']:.2%}"
            )
    return "\n".join([*lines, f"Report: {path}"])
