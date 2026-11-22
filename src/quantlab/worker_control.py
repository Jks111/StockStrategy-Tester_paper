"""In-process controller for the dashboard's signals-only observation loop."""

from datetime import datetime, timezone
from threading import Event, Lock, Thread

from . import paper_worker


class WorkerControl:
    def __init__(self, store):
        self.store = store
        self.lock = Lock()
        self.stop_event = Event()
        self.thread = None
        self.message = "Idle"
        paper_worker.initialize(store)

    def view(self):
        with self.store.connect() as db:
            configs = db.execute("""SELECT c.* FROM paper_signal_configs c JOIN paper_watchlist w
                ON w.version=c.version ORDER BY c.symbol""").fetchall()
            if not configs:
                configs = db.execute(
                    "SELECT * FROM paper_signal_configs ORDER BY version DESC LIMIT 1"
                ).fetchall()
            latest = []
            for c in configs:
                row = db.execute(
                    """SELECT bar_end,outcome,signal FROM paper_signal_decisions
                    WHERE config_version=? ORDER BY bar_end DESC LIMIT 1""",
                    (c["version"],),
                ).fetchone()
                hit = db.execute(
                    """SELECT bar_end,signal FROM paper_signal_decisions
                    WHERE config_version=? AND outcome='signal' ORDER BY bar_end DESC LIMIT 1""",
                    (c["version"],),
                ).fetchone()
                latest.append(
                    {
                        "symbol": c["symbol"],
                        "bar": dict(row) if row else None,
                        "signal": dict(hit) if hit else None,
                    }
                )
        with self.lock:
            return {
                "running": bool(self.thread and self.thread.is_alive()),
                "message": self.message,
                "feed": configs[0]["feed"] if configs else "iex",
                "start": configs[0]["start_session"] if configs else None,
                "rows": latest,
            }

    def command(self, body):
        action = body.get("action")
        with self.lock:
            if action == "stop":
                self.stop_event.set()
                return
            if self.thread and self.thread.is_alive():
                raise ValueError(
                    "Stop the dashboard worker and wait for the current check to finish first."
                )
            if action == "configure":
                paper_worker.configure(self.store, body["symbols"], body["feed"], body["start"])
                self.message = "Watchlist saved"
                return
            if action not in ("once", "start"):
                raise ValueError("Unknown worker action")
            self.stop_event.clear()
            self.message = "Checking completed bars"
            self.thread = Thread(target=self._run, args=(action == "start",), daemon=True)
            self.thread.start()

    def _run(self, repeat):
        while not self.stop_event.is_set():
            try:
                result = paper_worker.run_once(self.store)
                errors = [
                    f"{symbol}: {r['error']}"
                    for symbol, r in result.get("symbols", {}).items()
                    if "error" in r
                ]
                message = f"{datetime.now(timezone.utc):%H:%M UTC} · {result['decisions_added']} new decisions"
                if errors:
                    message += " · " + " / ".join(errors)
            except Exception as error:
                message = str(error)
                repeat = False
            with self.lock:
                self.message = message
            if not repeat or self.stop_event.wait(300):
                return
