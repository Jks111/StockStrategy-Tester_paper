import itertools
import json
import math
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from .engine import DEFAULT_RISK, backtest, validate
from .portfolio import backtest_portfolio
from .recent import batch_clock, batch_history, incompatibility, selection, snapshot, snapshot_data
from .storage import now


class Service:
    def __init__(self, store):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quantlab")
        self.tasks = {}
        self.lock = threading.Lock()
        self.cache = {}
        with store.connect() as c:
            c.executescript(
                "CREATE TABLE IF NOT EXISTS holdouts(dataset_id TEXT PRIMARY KEY, start TEXT, final_run TEXT); CREATE TABLE IF NOT EXISTS replay(id INTEGER PRIMARY KEY, payload TEXT);"
            )
            c.execute(
                "UPDATE runs SET status='interrupted',message='Server restarted; resubmit to rerun' WHERE status IN ('queued','running')"
            )

    def data(self, dataset_id):
        if dataset_id not in self.cache:
            if len(self.cache) > 2:
                self.cache.clear()
            self.cache[dataset_id] = self.store.load_bars(dataset_id)
        return self.cache[dataset_id]

    def holdout(self, metadata):
        with self.store.connect() as c:
            row = c.execute(
                "SELECT * FROM holdouts WHERE dataset_id=?", (metadata["id"],)
            ).fetchone()
            if row:
                return dict(row)
            dates = sorted(
                {b.session for series in self.data(metadata["id"]).values() for b in series}
            )
            start = dates[min(len(dates) - 1, int(len(dates) * 0.8))]
            c.execute("INSERT INTO holdouts VALUES(?,?,NULL)", (metadata["id"], start))
        return {"dataset_id": metadata["id"], "start": start, "final_run": None}

    def prepare(self, raw):
        meta = self.store.dataset(raw["dataset"])
        holdout = self.holdout(meta)
        if not raw.get("end") and raw.get("split") != "final_holdout":
            dates = sorted(
                {
                    b.session
                    for series in self.data(meta["id"]).values()
                    for b in series
                    if b.session < holdout["start"]
                }
            )
            if not dates:
                raise ValueError("Dataset needs more sessions before its reserved holdout")
            raw = {**raw, "end": dates[-1]}
        spec = validate(raw, meta)
        if spec["split"] == "final_holdout":
            if holdout["final_run"]:
                raise ValueError(
                    "Final holdout already used for this dataset; use a new future dataset"
                )
            spec["start"] = holdout["start"]
            spec["end"] = meta["end"]
        elif spec["end"] >= holdout["start"]:
            raise ValueError(
                f"Dates from {holdout['start']} are reserved for one final holdout evaluation"
            )
        return spec

    def run_one(self, run_id, *, raise_errors=False):
        store = self.store
        spec = store.run(run_id)["spec"]
        try:
            store.update_run(run_id, "running", 0, "Replaying completed bars")
            execute = backtest_portfolio if spec["strategy"] == "portfolio" else backtest
            result = execute(
                spec,
                store.dataset(spec["dataset"]),
                self.data(spec["dataset"]),
                store.events(),
                lambda p: store.update_run(run_id, "running", p, "Replaying completed bars"),
            )
            result["manifest"]["run_id"] = run_id
            store.save_result(run_id, result)
            store.update_run(run_id, "complete", 1, "", result["metrics"])
            return result
        except Exception as error:
            store.update_run(run_id, "failed", 0, str(error)[:400])
            if raise_errors:
                raise

    def recent(self, raw):
        bars, metadata, spec = snapshot(raw)
        with self.lock:
            dataset = self.store.save_bars(bars, metadata)
        run_id = self.store.create_run(validate(spec, dataset))
        self.run_one(run_id, raise_errors=True)
        return run_id

    def submit_recent_batch(self, raw):
        symbols, strategies, period, feed = selection(raw)
        mode = raw.get("mode", "standard")
        if mode not in ("standard", "deep"):
            raise ValueError("Choose a standard or deep stock backtest")
        windows = list(range(1, 51, 7)) if mode == "deep" else [0]
        if mode == "deep":
            period = "1w"

        def work(report_progress):
            as_of = batch_clock()
            history = batch_history(extra_days=windows[-1] - windows[0])
            batch_id = "recent-" + uuid.uuid4().hex[:9]
            snapshots = {}
            references = {}
            outcomes = []

            def dataset_for(symbol, strategy, days_ago):
                daily = strategy["timeframe"] == "1Day"
                key = symbol, daily, strategy["id"] == "residual_reversion", days_ago
                if key not in snapshots:
                    bars, meta, spec = snapshot_data(
                        symbol, strategy, period, feed, as_of, history, days_ago=days_ago
                    )
                    snapshots[key] = self.store.save_bars(bars, meta), spec
                return snapshots[key]

            def comparison(symbol, strategy, days_ago):
                timeframe = strategy["timeframe"]
                key = symbol, timeframe == "1Day", days_ago
                if key not in references:
                    try:
                        dataset, spec = dataset_for(
                            symbol, {"id": "buy_hold", "timeframe": timeframe}, days_ago
                        )
                        baseline = validate(
                            {
                                **spec,
                                "strategy": "buy_hold",
                                "split": "stock_reference",
                                "label": f"{symbol} buy and hold · {spec['start']} to {spec['end']}",
                                "risk": {"capital": 1000, "position_fraction": 1},
                            },
                            dataset,
                        )
                        run_id = self.store.create_run(baseline, batch_id)
                        result = self.run_one(run_id, raise_errors=True)
                        if not result["trades"]:
                            raise ValueError("Too few bars to enter and close the reference")
                        references[key] = {
                            "symbol": symbol,
                            "run_id": run_id,
                            "net_pnl": round(result["metrics"]["end_equity"] - 1000, 2) or 0,
                        }
                    except Exception as error:
                        references[key] = {"symbol": symbol, "message": str(error)[:400]}
                return references[key]

            for (window, days_ago), symbol, strategy in itertools.product(
                enumerate(windows, 1), symbols, strategies
            ):
                outcome = {
                    "symbol": symbol,
                    "strategy": strategy["id"],
                    "name": strategy["name"],
                    "window": window,
                }
                outcomes.append(outcome)
                try:
                    reason = incompatibility(symbol, strategy, period)
                    if reason:
                        outcome.update(status="skipped", message=reason)
                        continue
                    outcome["comparisons"] = [
                        comparison(symbol, strategy, days_ago),
                        comparison("SPY", strategy, days_ago),
                    ]
                    dataset, spec = dataset_for(symbol, strategy, days_ago)
                    spec = validate({**spec, "strategy": strategy["id"]}, dataset)
                    outcome.update(start=spec["start"], end=spec["end"])
                    run_id = self.store.create_run(spec, batch_id)
                    result = self.run_one(run_id, raise_errors=True)
                    metrics = result["metrics"]
                    winners = sorted(
                        (t for t in result["trades"] if t["net_pnl"] > 0),
                        key=lambda t: -t["net_pnl"],
                    )
                    losers = sorted(
                        (t for t in result["trades"] if t["net_pnl"] < 0),
                        key=lambda t: t["net_pnl"],
                    )
                    outcome.update(
                        status="complete",
                        run_id=run_id,
                        start=spec["start"],
                        end=spec["end"],
                        risk=spec["risk"],
                        costs=metrics["costs"],
                        drawdown=metrics["max_drawdown"],
                        trades=metrics["trades"],
                        wins=len(winners),
                        losses=len(losers),
                        win_rate=metrics["win_rate"],
                        net_pnl=round(metrics["end_equity"] - spec["risk"]["capital"], 2) or 0,
                        assumptions=result["warnings"],
                        top_trades=winners[:5],
                        bottom_trades=losers[:5],
                    )
                except Exception as error:
                    outcome.update(status="failed", message=str(error)[:400])
                finally:
                    report_progress(len(outcomes))
            report = {
                "batch_id": batch_id,
                "mode": mode,
                "as_of": as_of.isoformat(),
                "symbols": symbols,
                "strategies": strategies,
                "period": period,
                "feed": feed,
                "window_end_offsets_days": windows,
                "risk": {**DEFAULT_RISK, "capital": 1000},
                "outcomes": outcomes,
            }
            self.store.save_stock_batch(batch_id, report)
            return report

        return self.task(
            "stock backtest", work, total=len(symbols) * len(strategies) * len(windows)
        )

    def submit(self, raw, batch_id=None):
        spec = self.prepare(raw)
        run_id = self.store.create_run(spec, batch_id)
        if spec["split"] == "final_holdout":
            with self.store.connect() as c:
                cursor = c.execute(
                    "UPDATE holdouts SET final_run=? WHERE dataset_id=? AND final_run IS NULL",
                    (run_id, spec["dataset"]),
                )
                if cursor.rowcount != 1:
                    self.store.update_run(run_id, "failed", message="Holdout already reserved")
                    raise ValueError("Holdout already reserved")
        self.pool.submit(self.run_one, run_id)
        return run_id

    def submit_portfolio(self, raw):
        components = raw.get("components")
        if not isinstance(components, list) or not 2 <= len(components) <= 5:
            raise ValueError("Choose 2–5 strategy allocations")
        capital = float(raw.get("capital", 1000))
        weights = [float(c["weight"]) for c in components]
        if not math.isfinite(capital) or not 200 <= capital <= 1e9:
            raise ValueError("Portfolio capital must be between $200 and $1 billion")
        if any(not math.isfinite(w) or w <= 0 for w in weights) or abs(sum(weights) - 1) > 1e-8:
            raise ValueError("Positive allocation weights must total 100%")
        prepared = []
        for c, weight in zip(components, weights):
            child = c["spec"]
            if child.get("split", "exploratory") != "exploratory":
                raise ValueError("Combined testing currently supports exploratory dates only")
            child = self.prepare(
                {**child, "risk": {**child.get("risk", {}), "capital": capital * weight}}
            )
            prepared.append({"weight": weight, "spec": child})
        first = prepared[0]["spec"]
        if any(
            any(c["spec"][k] != first[k] for k in ("dataset", "start", "end")) for c in prepared
        ):
            raise ValueError("All allocations must use the same dataset and date range")
        spec = {
            **first,
            "strategy": "portfolio",
            "params": {},
            "components": prepared,
            "symbols": sorted({s for c in prepared for s in c["spec"]["symbols"]}),
            "risk": {**first["risk"], "capital": capital},
            "label": str(raw.get("label", "Combined portfolio"))[:120],
        }
        run_id = self.store.create_run(spec)
        self.pool.submit(self.run_one, run_id)
        return run_id

    def batch(self, raw, grid):
        if raw.get("split") == "final_holdout":
            raise ValueError("Final holdout allows only one frozen strategy, not a sweep")
        if not isinstance(grid, dict) or any(
            not isinstance(v, list) or not v for v in grid.values()
        ):
            raise ValueError("Grid must map parameters to nonempty lists")
        count = 1
        for v in grid.values():
            count *= len(v)
        if count > 500:
            raise ValueError("Maximum 500 variants per batch")
        specs = []
        for combination in itertools.product(*grid.values()):
            params = {**raw.get("params", {}), **dict(zip(grid, combination))}
            specs.append(self.prepare({**raw, "params": params}))
        batch_id = uuid.uuid4().hex[:12]
        return {
            "batch_id": batch_id,
            "runs": [self.submit(s, batch_id) for s in specs],
            "count": count,
        }

    def walk_forward(self, raw):
        spec = self.prepare(raw)
        dates = sorted(
            {
                b.session
                for series in self.data(spec["dataset"]).values()
                for b in series
                if spec["start"] <= b.session <= spec["end"]
            }
        )
        if len(dates) < 60:
            raise ValueError("Walk-forward needs at least 60 sessions")
        # Fixed parameters, no refitting or hidden winner selection. Nonoverlapping evaluation windows.
        batch_id = "wf-" + uuid.uuid4().hex[:9]
        runs = []
        for i in range(3):
            left = int(len(dates) * (0.4 + i * 0.2))
            right = min(len(dates) - 1, int(len(dates) * (0.6 + i * 0.2)) - 1)
            runs.append(
                self.submit(
                    {
                        **spec,
                        "start": dates[left],
                        "end": dates[right],
                        "split": "walk_forward",
                        "label": f"Walk-forward fold {i + 1}",
                    },
                    batch_id,
                )
            )
        return {"batch_id": batch_id, "runs": runs, "count": 3}

    def task(self, kind, operation, *, total=None):
        task_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.tasks[task_id] = {
                "id": task_id,
                "kind": kind,
                "status": "queued",
                "created": now(),
            }
            if total is not None:
                self.tasks[task_id]["progress"] = {"processed": 0, "total": total}

        def report_progress(processed):
            with self.lock:
                self.tasks[task_id]["progress"] = {"processed": processed, "total": total}

        def work():
            with self.lock:
                self.tasks[task_id]["status"] = "running"
            try:
                result = operation() if total is None else operation(report_progress)
                with self.lock:
                    self.tasks[task_id].update(status="complete", result=result)
            except Exception as error:
                with self.lock:
                    self.tasks[task_id].update(status="failed", error=str(error)[:400])

        self.pool.submit(work)
        return task_id

    def replay(self, raw=None, advance=False):
        with self.store.connect() as c:
            row = c.execute("SELECT payload FROM replay WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else None
        if raw:
            spec = self.prepare(raw)
            if spec["split"] == "final_holdout":
                raise ValueError("Replay cannot access final holdout")
            dates = sorted(
                {
                    b.session
                    for series in self.data(spec["dataset"]).values()
                    for b in series
                    if spec["start"] <= b.session <= spec["end"]
                }
            )
            state = {"spec": spec, "dates": dates, "index": -1, "result": None}
        if not state:
            return None
        if advance or raw:
            state["index"] = min(state["index"] + 1, len(state["dates"]) - 1)
            spec = {**state["spec"], "end": state["dates"][state["index"]]}
            state["result"] = backtest(
                spec,
                self.store.dataset(spec["dataset"]),
                self.data(spec["dataset"]),
                self.store.events(),
                liquidate_end=False,
            )
            with self.store.connect() as c:
                c.execute(
                    "INSERT OR REPLACE INTO replay VALUES(1,?)",
                    (json.dumps(state, allow_nan=False),),
                )
        return state
