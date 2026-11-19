"""Each module owns a strategy; the registry supplies discovery and defaults."""

from importlib import import_module

DEFINITIONS = [
    (
        "buy_hold",
        "Buy and hold",
        "baseline",
        "any",
        {},
        "Next-open allocation within position limits; unused capital stays in cash. Configure full allocation for a single-symbol benchmark.",
    ),
    (
        "opening_ema",
        "Opening candle · EMA 12",
        "intraday",
        "5Min",
        {"ema_length": 12, "atr_multiple": 2.0, "allow_short": False},
        "First 09:30–09:35 candle versus EMA; ATR trail and EMA momentum exit.",
    ),
    (
        "opening_range",
        "Opening range breakout",
        "intraday",
        "5Min",
        {"atr_multiple": 0.25, "target_multiple": 2.0, "allow_short": False},
        "09:30–09:45 range; one breakout per day before 15:00, exit by 15:55.",
    ),
    (
        "regression_trend",
        "Regression trend",
        "trend",
        "1Day",
        {"lookback": 40, "min_r2": 0.6, "atr_multiple": 3.0, "hold_bars": 60},
        "Positive log-price slope and stable fit; exit on slope loss or ATR trail.",
    ),
    (
        "mean_reversion",
        "Price mean reversion",
        "reversion",
        "1Day",
        {"lookback": 20, "entry_z": -2.0, "exit_z": 0.0, "hold_bars": 10, "atr_multiple": 2.0},
        "Buy below rolling mean; exit on recovery or a time/volatility limit.",
    ),
    (
        "residual_reversion",
        "Market residual reversion",
        "reversion",
        "1Day",
        {"lookback": 60, "entry_z": -2.0, "hold_bars": 10, "atr_multiple": 2.0},
        "Rolling stock/market return regression; trade cumulative residual deviation. Research prototype.",
    ),
    (
        "overnight",
        "Close to next open",
        "overnight",
        "1Day",
        {"weekday": "all"},
        "Calendar-only eligibility; idealized official-close fill, next session open exit.",
    ),
    (
        "overnight_weekend",
        "Weekend close to open",
        "overnight",
        "1Day",
        {"weekday": "friday"},
        "Friday close to next trading session open; compare per trade and calendar-day risk.",
    ),
    (
        "insider_purchase",
        "Public insider purchase",
        "insider",
        "1Day",
        {"event_days": 10, "hold_bars": 20, "min_value": 10000, "atr_multiple": 3.0},
        "Non-amended code P filing known before decision; market entry, never insider price.",
    ),
    (
        "insider_cluster",
        "Cluster insider purchases",
        "insider",
        "1Day",
        {
            "event_days": 14,
            "min_insiders": 2,
            "hold_bars": 20,
            "min_value": 10000,
            "atr_multiple": 3.0,
        },
        "Distinct disclosed buyers in a trailing window.",
    ),
    (
        "insider_overnight",
        "Insider + overnight · V2",
        "combined",
        "1Day",
        {"event_days": 10, "min_value": 10000, "weekday": "all"},
        "Recent public purchase plus calendar close-to-open; filing cutoff five minutes before close.",
    ),
    (
        "insider_trend",
        "Insider + regression · V2",
        "combined",
        "1Day",
        {
            "event_days": 14,
            "min_value": 10000,
            "lookback": 40,
            "min_r2": 0.5,
            "hold_bars": 30,
            "atr_multiple": 3.0,
        },
        "Public purchase and positive regression trend.",
    ),
    (
        "insider_pullback",
        "Insider + pullback · V2",
        "combined",
        "1Day",
        {
            "event_days": 20,
            "min_value": 10000,
            "lookback": 20,
            "entry_z": -1.0,
            "exit_z": 0.0,
            "hold_bars": 10,
            "atr_multiple": 2.0,
        },
        "Public purchase and price mean-reversion entry.",
    ),
    (
        "insider_opening",
        "Insider + opening EMA · V2",
        "combined",
        "5Min",
        {
            "event_days": 10,
            "min_value": 10000,
            "ema_length": 12,
            "atr_multiple": 2.0,
            "allow_short": False,
        },
        "Public purchase gates the opening EMA signal; long only.",
    ),
    (
        "profit_target",
        "Fixed cash profit target",
        "experiment",
        "1Day",
        {"target_cash": 5.0, "hold_bars": 60, "atr_multiple": 3.0},
        "Net cash target after modelled order costs, in dataset currency, with loss and time limits. No guarantee of reaching €5.",
    ),
]


def catalog():
    return [
        {"id": i, "name": n, "family": f, "timeframe": t, "defaults": p, "description": d}
        for i, n, f, t, p, d in DEFINITIONS
    ]


def definition(strategy_id):
    for row in catalog():
        if row["id"] == strategy_id:
            return row
    raise ValueError("Unknown strategy")


def load(strategy_id):
    definition(strategy_id)
    return import_module(f"quantlab.strategies.{strategy_id}").evaluate
