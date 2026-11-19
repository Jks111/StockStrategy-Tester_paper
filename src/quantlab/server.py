import csv
import io
import json
import mimetypes
import re
import secrets
import socket
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .data.audit import audit
from .data.providers import (
    Alpaca,
    credentials,
    fetch_form4,
    import_legacy,
    parse_csv,
    parse_insider_csv,
    request_json,
)
from .data.synthetic import build_sample
from .data.trading212_demo import snapshot as demo_snapshot
from .jobs import Service
from .markets import market_hours
from .storage import Store
from .strategies import catalog

UI = Path(__file__).parent / "ui"


class LocalServer(ThreadingHTTPServer):
    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows SO_REUSEADDR lets two servers accept requests on the same port.
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def create_server(port=8765, store=None):
    store = store or Store()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, value, status=200, mime="application/json", filename=None):
            body = (
                json.dumps(value, allow_nan=False).encode()
                if mime == "application/json"
                else value.encode()
                if isinstance(value, str)
                else value
            )
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'",
            )
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            host = self.headers.get("Host", "")
            if host not in (
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            ):
                self.send({"error": "Local host required"}, 403)
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in (
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            ):
                self.send({"error": "Cross-origin requests denied"}, 403)
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.send({"error": "Cross-site requests denied"}, 403)
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            try:
                parsed = urlparse(self.path)
                path = parsed.path
                q = parse_qs(parsed.query)
                if path == "/api/bootstrap":
                    env = credentials()
                    self.send(
                        {
                            "token": token,
                            "market_hours": market_hours(),
                            "strategies": catalog(),
                            "datasets": [
                                {**d, "holdout": service.holdout(d)} for d in store.datasets()
                            ],
                            "connections": {
                                "alpaca": bool(
                                    env.get("ALPACA_API_KEY") and env.get("ALPACA_API_SECRET")
                                ),
                                "trading212": bool(
                                    env.get("TRADING212_DEMO_API_KEY")
                                    and env.get("TRADING212_DEMO_API_SECRET")
                                ),
                                "sec": bool(
                                    env.get("SEC_USER_AGENT")
                                    and "example.com" not in env.get("SEC_USER_AGENT", "")
                                ),
                            },
                            "version": "0.1.0",
                        }
                    )
                elif path == "/api/runs":
                    self.send(store.runs())
                elif path == "/api/database":
                    self.send(store.overview())
                elif path == "/api/stock-backtest-history":
                    self.send(store.stock_backtest_history())
                elif path == "/api/stock-backtest-batches":
                    self.send(store.stock_batches())
                elif path.startswith("/api/stock-backtest-batches/"):
                    batch_id = path.rsplit("/", 1)[1]
                    report = store.stock_batch(batch_id)
                    filename = f"{batch_id}.json" if q.get("format") == ["json"] else None
                    self.send(report, filename=filename)
                elif path == "/api/tasks":
                    with service.lock:
                        self.send(
                            [
                                {k: v for k, v in task.items() if k != "result"}
                                if task["kind"] == "stock backtest"
                                else task
                                for task in service.tasks.values()
                            ]
                        )
                elif path.startswith("/api/tasks/"):
                    with service.lock:
                        task = dict(service.tasks[path.rsplit("/", 1)[1]])
                    self.send(task)
                elif path == "/api/insiders":
                    self.send([asdict(e) for e in store.events()])
                elif path == "/api/replay":
                    self.send(service.replay())
                elif path == "/api/bars":
                    data = service.data(q["dataset"][0])
                    symbol = q["symbol"][0]
                    if symbol not in data:
                        raise ValueError("Symbol not found")
                    limit = min(int(q.get("limit", ["1200"])[0]), 20000)
                    if limit < 1:
                        raise ValueError("Invalid bar limit")
                    self.send([asdict(b) for b in data[symbol][-limit:]])
                elif path.startswith("/api/results/"):
                    run_id = path.split("/")[-1]
                    result = store.result(run_id)
                    if q.get("format") == ["csv"]:
                        out = io.StringIO()
                        trades = result["trades"]
                        if trades:
                            writer = csv.DictWriter(out, fieldnames=list(trades[0]))
                            writer.writeheader()
                            writer.writerows(trades)
                        self.send(out.getvalue(), mime="text/csv", filename=f"{run_id}-trades.csv")
                    else:
                        self.send(result)
                elif path == "/api/health":
                    self.send({"status": "ok", "mode": "research-only"})
                else:
                    name = "index.html" if path == "/" else path.lstrip("/")
                    if name not in ("index.html", "app.js", "style.css"):
                        self.send({"error": "Not found"}, 404)
                        return
                    self.send(
                        (UI / name).read_bytes(), mime=mimetypes.guess_type(name)[0] or "text/plain"
                    )
            except (ValueError, KeyError, TypeError) as error:
                self.send({"error": str(error)}, 400)
            except Exception:
                self.send({"error": "Request failed; check local server console"}, 500)

        def do_POST(self):
            if not self.allowed():
                return
            if not secrets.compare_digest(self.headers.get("X-Quantlab-Token", ""), token):
                self.send({"error": "Reload the app to refresh the session"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 20_000_000:
                    raise ValueError("Request must be under 20 MB")
                body = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path == "/api/run":
                    self.send({"run_id": service.submit(body)}, 202)
                elif path == "/api/recent":
                    self.send({"run_id": service.recent(body)})
                elif path == "/api/recent-batch":
                    self.send({"task_id": service.submit_recent_batch(body)}, 202)
                elif path == "/api/batch":
                    self.send(service.batch(body["spec"], body["grid"]), 202)
                elif path == "/api/portfolio":
                    self.send({"run_id": service.submit_portfolio(body)}, 202)
                elif path == "/api/audit":
                    dataset_id = body["dataset"]
                    report = audit(store.dataset(dataset_id), service.data(dataset_id))
                    with store.connect() as c:
                        c.execute(
                            "CREATE TABLE IF NOT EXISTS data_audits(dataset_id TEXT PRIMARY KEY, report TEXT NOT NULL)"
                        )
                        c.execute(
                            "INSERT OR REPLACE INTO data_audits VALUES(?,?)",
                            (dataset_id, json.dumps(report)),
                        )
                    self.send(report)
                elif path == "/api/walk-forward":
                    self.send(service.walk_forward(body), 202)
                elif path == "/api/sample":
                    self.send({"task_id": service.task("sample", lambda: build_sample(store))}, 202)
                elif path == "/api/import/bars":
                    if body.get("timeframe") not in ("1Day", "5Min", "1Min"):
                        raise ValueError("Choose 1Day, 5Min or 1Min")
                    data = store.save_bars(
                        parse_csv(body["text"]),
                        {
                            "name": str(body.get("name", "CSV import"))[:120],
                            "source": "csv",
                            "feed": "user",
                            "timeframe": body["timeframe"],
                            "currency": "USD",
                            "adjustment": "user-supplied",
                            "quality": [
                                "CSV provenance, session calendar, corporate actions and historical universe require verification."
                            ],
                        },
                    )
                    self.send(data)
                elif path == "/api/import/insiders":
                    self.send({"added": store.put_events(parse_insider_csv(body["text"]))})
                elif path == "/api/import/legacy":
                    self.send({"added": store.put_events(import_legacy(body["path"]))})
                elif path == "/api/import/sec":
                    cik = str(int(body["cik"]))
                    limit = max(1, min(25, int(body.get("limit", 10))))
                    self.send(
                        {
                            "task_id": service.task(
                                "SEC filings",
                                lambda: {"added": store.put_events(fetch_form4(cik, limit))},
                            )
                        },
                        202,
                    )
                elif path == "/api/import/alpaca":
                    symbols = [s.strip().upper() for s in body["symbols"].split(",") if s.strip()]
                    if (
                        not symbols
                        or len(symbols) > 30
                        or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", s) for s in symbols)
                    ):
                        raise ValueError("Enter 1–30 valid ticker symbols")
                    start, end = body["start"], body["end"]
                    timeframe = body.get("timeframe", "1Day")
                    feed = body.get("feed", "sip")
                    from datetime import date

                    if date.fromisoformat(start) > date.fromisoformat(end):
                        raise ValueError("Invalid dates")

                    def download():
                        data = Alpaca().history(symbols, start, end, timeframe, feed)
                        return store.save_bars(
                            data,
                            {
                                "name": f"Alpaca {timeframe} · {', '.join(symbols)}",
                                "source": "alpaca",
                                "feed": feed,
                                "timeframe": timeframe,
                                "currency": "USD",
                                "adjustment": "split",
                                "quality": [
                                    "Historical requests end at least 16 minutes ago. Real-time IEX is an incomplete exchange feed.",
                                    "Split adjusted; cash dividends not included. Current ticker universe can introduce survivorship bias.",
                                ],
                            },
                        )

                    self.send({"task_id": service.task("Alpaca history", download)}, 202)
                elif path == "/api/quotes":
                    symbols = body.get("symbols", [])
                    if (
                        not isinstance(symbols, list)
                        or not 1 <= len(symbols) <= 30
                        or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", s) for s in symbols)
                    ):
                        raise ValueError("Invalid ticker list")
                    self.send(
                        {"task_id": service.task("IEX quotes", lambda: Alpaca().quotes(symbols))},
                        202,
                    )
                elif path == "/api/paper-account":
                    self.send(
                        {
                            "task_id": service.task(
                                "Paper account read",
                                lambda: request_json(
                                    "https://paper-api.alpaca.markets/v2/account", Alpaca().headers
                                ),
                            )
                        },
                        202,
                    )
                elif path == "/api/trading212-demo":
                    self.send(
                        {"task_id": service.task("Trading 212 demo read", demo_snapshot)}, 202
                    )
                elif path == "/api/replay":
                    self.send(service.replay(body.get("spec"), body.get("advance", False)))
                else:
                    self.send({"error": "Not found"}, 404)
            except (ValueError, KeyError, TypeError) as error:
                self.send({"error": str(error)}, 400)
            except Exception as error:
                self.send({"error": str(error)[:300]}, 400)

    server = LocalServer(("127.0.0.1", port), Handler)
    try:
        service = Service(store)
    except Exception:
        server.server_close()
        raise
    server.service = service
    return server
