import argparse
import errno
import json
import threading
import webbrowser
from http.client import HTTPConnection, HTTPException
from pathlib import Path

from . import paper_worker
from .data.synthetic import build_sample
from .server import create_server
from .storage import STATE, Store


def main():
    parser = argparse.ArgumentParser(description="Local stock strategy research; no live orders")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true")
    commands.add_parser("sample")
    experiment = commands.add_parser("spy-open-close", help="Simulate the SPY timing experiment")
    source = experiment.add_mutually_exclusive_group(required=True)
    source.add_argument("--download", action="store_true", help="Fetch 2017–2025 SPY SIP daily bars")
    source.add_argument("--dataset", help="Replay a cached QuantLab SPY SIP daily dataset")
    worker = commands.add_parser("paper-worker", help="Observe completed bars once")
    action = worker.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--configure", nargs="+", metavar="TICKER", help="Set the watchlist using spaces or commas"
    )
    action.add_argument("--once", action="store_true", help="Process the current configuration")
    worker.add_argument("--start", metavar="YYYY-MM-DD", help="First US session to record")
    worker.add_argument("--feed", choices=["iex", "sip"], help="Alpaca market data feed")
    worker.add_argument("--state-dir", type=Path, default=STATE, help="QuantLab state directory")
    args = parser.parse_args()
    if args.command == "spy-open-close":
        from .spy_open_close import run

        try:
            print(run(Store(), args.dataset))
        except ValueError as error:
            experiment.exit(1, f"{error}\n")
        return
    if args.command == "paper-worker":
        if args.configure is not None and not (args.start and args.feed):
            worker.error("--configure requires --start and --feed")
        if args.once and (args.start or args.feed):
            worker.error("--start and --feed apply to --configure")
        store = Store(args.state_dir)
        try:
            if args.configure is not None:
                version = paper_worker.configure(store, args.configure, args.feed, args.start)
                result = (
                    {"symbols": version}
                    if isinstance(version, dict)
                    else {"config_version": version}
                )
            else:
                result = paper_worker.run_once(store)
        except ValueError as error:
            worker.exit(1, f"{error}\n")
        print(json.dumps(result, indent=2))
        return
    if args.command == "sample":
        print(json.dumps(build_sample(Store()), indent=2))
        return
    port = getattr(args, "port", 8765)
    store = Store()
    if not store.datasets():
        print("Generating clearly labelled synthetic starter datasets...", flush=True)
        build_sample(store)
    url = f"http://127.0.0.1:{port}"
    try:
        server = create_server(port, store)
    except OSError as error:
        if error.errno != errno.EADDRINUSE and getattr(error, "winerror", None) != 10048:
            raise
        probe = HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            probe.request("GET", "/api/health")
            response = probe.getresponse()
            ready = response.status == 200 and json.loads(response.read(1024)) == {
                "status": "ok", "mode": "research-only"
            }
        except (OSError, HTTPException, ValueError):
            ready = False
        finally:
            probe.close()
        if not ready:
            parser.exit(1, f"Port {port} is occupied and QuantLab could not be verified there. Check the existing server window.\n")
        print(f"QuantLab is already running at {url}.")
        if getattr(args, "open", False):
            webbrowser.open(url)
        return
    print(f"QuantLab at {url} - research-only. Ctrl+C to stop.", flush=True)
    if getattr(args, "open", False):
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        while True:
            try:
                server.serve_forever()
                break
            except KeyboardInterrupt:
                warnings = server.basket.stop_warnings()
                if not warnings:
                    break
                print("\nWARNING: " + " ".join(warnings), flush=True)
                try:
                    if input("Stop QuantLab anyway? [y/N] ").strip().lower() in ("y", "yes"):
                        break
                except EOFError:
                    break
                print("QuantLab continues running.", flush=True)
    finally:
        server.server_close()
        server.service.pool.shutdown(wait=False, cancel_futures=True)
