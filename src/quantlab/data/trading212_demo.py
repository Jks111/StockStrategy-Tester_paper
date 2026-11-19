"""Fixed demo hostname and a GET-only surface. No order methods."""

import base64

from .providers import credentials, request_json


def snapshot():
    env = credentials()
    key = env.get("TRADING212_DEMO_API_KEY")
    secret = env.get("TRADING212_DEMO_API_SECRET")
    if not key or not secret:
        raise ValueError("Set TRADING212_DEMO_API_KEY and TRADING212_DEMO_API_SECRET in .env")
    headers = {"Authorization": "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()}
    base = "https://demo.trading212.com/api/v0"
    return {
        "environment": "Trading 212 DEMO — read only",
        "summary": request_json(base + "/equity/account/summary", headers),
        "positions": request_json(base + "/equity/positions", headers),
    }
