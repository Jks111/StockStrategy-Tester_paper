import argparse
import json
import threading
import webbrowser

from .data.synthetic import build_sample
from .server import create_server
from .storage import Store


def main():
    parser = argparse.ArgumentParser(description="Local stock strategy research; no live orders")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true")
    commands.add_parser("sample")
    args = parser.parse_args()
    if args.command == "sample":
        print(json.dumps(build_sample(Store()), indent=2))
        return
    port = getattr(args, "port", 8765)
    store = Store()
    if not store.datasets():
        print("Generating clearly labelled synthetic starter datasets...", flush=True)
        build_sample(store)
    server = create_server(port, store)
    url = f"http://127.0.0.1:{port}"
    print(f"QuantLab at {url} - research-only. Ctrl+C to stop.", flush=True)
    if getattr(args, "open", False):
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.service.pool.shutdown(wait=False, cancel_futures=True)
