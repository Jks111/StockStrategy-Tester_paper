"""Integration check: all 15 modules and a 200-variant synthetic sweep."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from quantlab.jobs import Service
from quantlab.storage import ROOT, Store, now
from quantlab.strategies import catalog


def main():
    store = Store()
    service = Service(store)
    sample = {d["timeframe"]: d for d in store.datasets() if d["source"] == "synthetic"}
    if not {"1Day", "5Min"} <= sample.keys():
        raise SystemExit("Create both sample datasets first")
    identifiers = []
    started = time.monotonic()
    for strategy in catalog():
        d = sample["5Min" if strategy["timeframe"] == "5Min" else "1Day"]
        identifiers.append(
            service.submit(
                {
                    "strategy": strategy["id"],
                    "dataset": d["id"],
                    "symbols": d["symbols"],
                    "label": "Verification · " + strategy["name"],
                }
            )
        )
    d = sample["1Day"]
    batch = service.batch(
        {
            "strategy": "regression_trend",
            "dataset": d["id"],
            "symbols": ["SPY"],
            "start": "2025-01-01",
            "label": "Synthetic verification sweep",
        },
        {
            "lookback": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
            "min_r2": [0.2, 0.4, 0.6, 0.8],
            "atr_multiple": [1, 2, 3, 4, 5],
        },
    )
    identifiers.extend(batch["runs"])
    print(f"Queued {len(identifiers)} synthetic verification runs", flush=True)
    service.pool.shutdown(wait=True)
    rows = [store.run(i) for i in identifiers]
    failed = [
        {"id": r["id"], "strategy": r["spec"]["strategy"], "error": r["message"]}
        for r in rows
        if r["status"] != "complete"
    ]
    report = {
        "created": now(),
        "data": "SYNTHETIC SOFTWARE VERIFICATION, NOT STRATEGY EVIDENCE",
        "modules": len(catalog()),
        "sweep_variants": batch["count"],
        "runs": len(rows),
        "complete": len(rows) - len(failed),
        "failures": failed,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "batch_id": batch["batch_id"],
        "run_ids": identifiers,
    }
    (ROOT / "var" / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf8")
    print(json.dumps({k: v for k, v in report.items() if k != "run_ids"}, indent=2), flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
