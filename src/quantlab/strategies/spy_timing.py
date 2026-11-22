"""Inference for the SPY models fitted on 2017–2023 consolidated daily bars."""

from functools import lru_cache
from pathlib import Path

import numpy as np

from ..domain import Signal

TRAIN_END = "2023-12-29"
MODEL_FILE = Path(__file__).with_name("spy_timing_2023.npz")
FILL_FEE = 0.01


def features(bars):
    close = np.array([b.close for b in bars])
    volume = np.array([b.volume for b in bars[-20:]])
    return (
        close[-1] / close[-2] - 1,
        close[-1] / close[-6] - 1,
        close[-1] / close[-20:].mean() - 1,
        np.std(np.diff(np.log(close)), ddof=1),
        volume[-1] / volume.mean() - 1,
    )


def break_even(budget, side_cost, fee):
    return (1 + 2 * fee / budget) * (1 + side_cost) / (1 - side_cost) - 1


@lru_cache(maxsize=1)
def weights():
    with np.load(MODEL_FILE, allow_pickle=False) as archive:
        return dict(archive)


def predict(x, arm):
    w = weights()
    if arm == "linear":
        return float(np.dot(x, w["coef"]) + w["intercept"])
    # sklearn's trees compare float32 inputs against float64 split thresholds.
    x = np.asarray(x, dtype=np.float32)
    trees = np.arange(len(w["left"]))
    nodes = np.zeros(len(trees), dtype=int)
    active = trees
    while active.size:
        node = nodes[active]
        active = active[w["left"][active, node] != -1]
        node = nodes[active]
        left = x[w["feature"][active, node]] <= w["threshold"][active, node]
        nodes[active] = np.where(left, w["left"][active, node], w["right"][active, node])
    return float(w["value"][trees, nodes].mean())


def signal(ctx, arm):
    bars = ctx.history[-21:]
    if len(bars) < 21 or not any(b.volume > 0 for b in bars[-20:]):
        return None
    expected = predict(features(bars), arm)
    if expected > ctx.params["_break_even"]:
        return Signal(1, f"Forecast {expected:.3%} exceeds round-trip cost", exit_mode="session")
    return None
