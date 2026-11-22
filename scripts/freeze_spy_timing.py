"""Rebuild the numeric SPY inference weights from the research snapshot."""

import argparse
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quantlab.spy_open_close import FOREST, check_snapshot, samples
from quantlab.storage import Store
from quantlab.strategies.spy_timing import MODEL_FILE, TRAIN_END


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    args = parser.parse_args()
    store = Store()
    bars = store.load_bars(args.dataset)["SPY"]
    check_snapshot(bars, store.dataset(args.dataset))
    train = samples([bar for bar in bars if bar.session <= TRAIN_END])
    x, y = [r.features for r in train], [r.target for r in train]
    linear = LinearRegression().fit(x, y)
    trees = [t.tree_ for t in RandomForestRegressor(**FOREST, n_jobs=1).fit(x, y).estimators_]
    length = max(t.node_count for t in trees)
    arrays = {}
    for key, attr in (
        ("left", "children_left"),
        ("right", "children_right"),
        ("feature", "feature"),
        ("threshold", "threshold"),
        ("value", "value"),
    ):
        arrays[key] = np.stack(
            [np.pad(getattr(t, attr).ravel(), (0, length - t.node_count)) for t in trees]
        )
    np.savez_compressed(
        MODEL_FILE,
        **arrays,
        coef=linear.coef_,
        intercept=linear.intercept_,
        train_end=TRAIN_END,
        dataset=args.dataset,
    )


if __name__ == "__main__":
    main()
