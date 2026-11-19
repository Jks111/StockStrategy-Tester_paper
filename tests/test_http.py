import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_research import TEST_ROOT, bar

from quantlab.server import create_server
from quantlab.storage import Store


class RecentClock(datetime):
    @classmethod
    def now(cls, tz=None):
        timestamp = datetime(2026, 3, 18, 15, 2, tzinfo=timezone.utc)
        return timestamp.astimezone(tz) if tz else timestamp.replace(tzinfo=None)


class RecentAlpacaFixture:
    def __init__(self, earliest=date(2026, 2, 17)):
        self.earliest = earliest
        self.sessions = []
        self.bar_requests = []

    def request_json(self, url, headers):
        request = urllib.parse.urlsplit(url)
        params = dict(urllib.parse.parse_qsl(request.query))
        if request.path == "/v2/calendar":
            start = max(date.fromisoformat(params["start"]), self.earliest)
            end = date.fromisoformat(params["end"])
            self.sessions = [
                day.isoformat()
                for offset in range((end - start).days + 1)
                if (day := start + timedelta(days=offset)).weekday() < 5
            ]
            return [{"date": day, "open": "09:30", "close": "16:00"} for day in self.sessions]
        if request.path != "/v2/stocks/bars":
            raise AssertionError(f"Unexpected Alpaca request: {request.path}")
        self.bar_requests.append(params)
        rows = []
        for day in self.sessions:
            midnight = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("America/New_York"))
            # Include premarket, after-hours and unfinished bars to exercise provider filtering.
            minutes = (
                range(565, 966, int(params["timeframe"][:-3]))
                if params["timeframe"] != "1Day"
                else (0,)
            )
            for minute in minutes:
                stamp = midnight + timedelta(minutes=minute)
                rows.append(
                    {"t": stamp.isoformat(), "o": 100, "h": 101, "l": 99, "c": 100, "v": 1000}
                )
        return {
            "bars": {
                symbol: rows for symbol in ("AAPL", "SPY") if symbol in params["symbols"].split(",")
            }
        }


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        cls.store = Store(cls.temp.name)
        cls.store.save_bars(
            [bar("2026-01-05"), bar("2026-01-06"), bar("2026-01-07")],
            {"source": "csv", "timeframe": "1Day"},
        )
        cls.server = create_server(0, cls.store)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.server.service.pool.shutdown(wait=True)
        cls.temp.cleanup()

    def request(self, path, body=None, headers=None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers or {},
        )
        return urllib.request.urlopen(request, timeout=5)

    def test_bootstrap_has_no_credentials(self):
        with self.request("/api/bootstrap") as response:
            body = json.load(response)
        self.assertIn("token", body)
        self.assertNotIn("ALPACA_API_SECRET", json.dumps(body))
        self.assertEqual(len(body["strategies"]), 15)

    def test_post_requires_session_token(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/sample", {})
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_duplicate_listener_does_not_initialize_service(self):
        with patch("quantlab.server.Service") as service:
            with self.assertRaises(OSError):
                create_server(self.port, self.store)
            service.assert_not_called()

    def test_recent_rejects_unsupported_input(self):
        with self.request("/api/bootstrap") as response:
            headers = {"X-Quantlab-Token": json.load(response)["token"]}
        valid = {"symbol": "AAPL", "strategy": "buy_hold", "period": "1w", "feed": "iex"}
        with patch("quantlab.data.providers.request_json") as provider:
            for field, value in (
                ("symbol", "AAPL,MSFT"),
                ("strategy", "unknown_strategy"),
                ("period", "5w"),
                ("feed", "unknown_feed"),
            ):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        self.request("/api/recent", {**valid, field: value}, headers)
                    with error.exception as response:
                        self.assertEqual(response.code, 400)
                        self.assertIn("error", json.load(response))
            provider.assert_not_called()

    def test_recent_completes_synchronously_for_each_period_and_feed(self):
        with self.request("/api/bootstrap") as response:
            headers = {"X-Quantlab-Token": json.load(response)["token"]}
        alpaca = RecentAlpacaFixture()
        credentials = {"ALPACA_API_KEY": "test-key", "ALPACA_API_SECRET": "test-secret"}
        with (
            patch("quantlab.recent.datetime", RecentClock),
            patch("quantlab.data.providers.credentials", return_value=credentials),
            patch("quantlab.data.providers.request_json", side_effect=alpaca.request_json),
        ):
            expected_starts = {
                "today": "2026-03-18",
                "1w": "2026-03-12",
                "2w": "2026-03-05",
                "3w": "2026-02-26",
                "4w": "2026-02-19",
            }
            for period, start in expected_starts.items():
                for feed in ("iex", "sip"):
                    with self.subTest(period=period, feed=feed):
                        strategy = "opening_ema" if period == "today" else "regression_trend"
                        payload = {
                            "symbol": "AAPL",
                            "strategy": strategy,
                            "period": period,
                            "feed": feed,
                        }
                        with self.request("/api/recent", payload, headers) as response:
                            self.assertEqual(response.status, 200)
                            body = json.load(response)
                        self.assertEqual(set(body), {"run_id"})
                        self.assertIsInstance(body["run_id"], str)
                        self.assertTrue(body["run_id"])
                        with self.request(f"/api/results/{body['run_id']}") as response:
                            result = json.load(response)
                        query = alpaca.bar_requests[-1]
                        self.assertEqual(query["symbols"], "AAPL")
                        self.assertEqual(query["feed"], feed)
                        self.assertEqual(result["manifest"]["run_id"], body["run_id"])
                        self.assertEqual(result["spec"]["strategy"], strategy)
                        self.assertEqual(result["spec"]["symbols"], ["AAPL"])
                        self.assertEqual(result["spec"]["start"], start)
                        dataset = result["manifest"]["dataset"]
                        self.assertLess(dataset["start"], result["spec"]["start"])
                        sessions = [point["session"] for point in result["equity"]]
                        if period == "today":
                            self.assertEqual(query["timeframe"], "5Min")
                            self.assertEqual(sessions, ["2026-03-18"])
                            self.assertEqual(dataset["timeframe"], "5Min")
                            with self.request(
                                f"/api/bars?dataset={dataset['id']}&symbol=AAPL"
                            ) as response:
                                bars = [
                                    b for b in json.load(response) if b["session"] == sessions[0]
                                ]
                            self.assertEqual(len(bars), 18 if feed == "iex" else 15)
                            self.assertEqual(bars[0]["time"], "2026-03-18T13:30:00+00:00")
                            end = "14:55" if feed == "iex" else "14:40"
                            self.assertEqual(bars[-1]["time"], f"2026-03-18T{end}:00+00:00")
                        else:
                            self.assertEqual(dataset["timeframe"], "1Day")
                            self.assertEqual(len(sessions), 5 * int(period[0]) - 1)
                            self.assertEqual(sessions[-1], "2026-03-17")

    def test_recent_batch_isolates_failures_and_reuses_downloads(self):
        with self.request("/api/bootstrap") as response:
            headers = {"X-Quantlab-Token": json.load(response)["token"]}
        alpaca = RecentAlpacaFixture()
        with (
            patch("quantlab.recent.datetime", RecentClock),
            patch(
                "quantlab.data.providers.credentials",
                return_value={
                    "ALPACA_API_KEY": "test-key",
                    "ALPACA_API_SECRET": "test-secret",
                },
            ),
            patch("quantlab.data.providers.request_json", side_effect=alpaca.request_json),
        ):
            with self.request(
                "/api/recent-batch",
                {
                    "symbols": ["MSFT", "AAPL", "aapl"],
                    "strategies": ["regression_trend", "opening_ema", "opening_range"],
                    "period": "today",
                    "feed": "iex",
                },
                headers,
            ) as response:
                self.assertEqual(response.status, 202)
                task_id = json.load(response)["task_id"]
            for _ in range(100):
                with self.request(f"/api/tasks/{task_id}") as response:
                    task = json.load(response)
                if task["status"] in ("complete", "failed"):
                    break
                time.sleep(0.05)
            self.assertEqual(task["status"], "complete", task.get("error"))
            self.assertEqual(task["progress"], {"processed": 6, "total": 6})
            outcomes = task["result"]["outcomes"]
            self.assertEqual(
                [r["status"] for r in outcomes],
                ["skipped", "failed", "failed", "skipped", "complete", "complete"],
            )
            self.assertEqual([q["symbols"] for q in alpaca.bar_requests], ["MSFT", "SPY", "AAPL"])
            with self.request("/api/stock-backtest-history") as response:
                history = {row["run_id"]: row for row in json.load(response)}
            for row in outcomes[-2:]:
                with self.request(f"/api/results/{row['run_id']}") as response:
                    result = json.load(response)
                self.assertEqual(result["spec"]["symbols"], ["AAPL"])
                self.assertEqual(result["spec"]["strategy"], row["strategy"])
                self.assertEqual(result["spec"]["risk"]["capital"], 1000)
                self.assertEqual(row["net_pnl"], round(result["metrics"]["end_equity"] - 1000, 2))
                self.assertEqual((row["trades"], row["wins"], row["win_rate"]), (0, 0, None))
                saved = history[row["run_id"]]
                for field in ("symbol", "strategy", "start", "end", "net_pnl", "wins", "win_rate"):
                    self.assertEqual(saved[field], row[field])
                self.assertEqual(saved["feed"], "iex")

    def test_deep_backtest_saves_windows_and_matched_references(self):
        with self.request("/api/bootstrap") as response:
            headers = {"X-Quantlab-Token": json.load(response)["token"]}
        alpaca = RecentAlpacaFixture(date(2026, 1, 1))
        with (
            patch("quantlab.recent.datetime", RecentClock),
            patch(
                "quantlab.data.providers.credentials",
                return_value={
                    "ALPACA_API_KEY": "test-key",
                    "ALPACA_API_SECRET": "test-secret",
                },
            ),
            patch("quantlab.data.providers.request_json", side_effect=alpaca.request_json),
        ):
            with self.request(
                "/api/recent-batch",
                {
                    "symbols": ["AAPL"],
                    "strategies": ["opening_ema"],
                    "period": "today",
                    "mode": "deep",
                    "feed": "iex",
                },
                headers,
            ) as response:
                task_id = json.load(response)["task_id"]
            for _ in range(150):
                with self.request(f"/api/tasks/{task_id}") as response:
                    task = json.load(response)
                if task["status"] in ("complete", "failed"):
                    break
                time.sleep(0.05)
        self.assertEqual(task["status"], "complete", task.get("error"))
        self.assertEqual(task["progress"], {"processed": 8, "total": 8})
        report = task["result"]
        with self.request(
            f"/api/stock-backtest-batches/{report['batch_id']}?format=json"
        ) as response:
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertEqual(json.load(response), report)
        with self.request("/api/stock-backtest-batches") as response:
            self.assertIn(report["batch_id"], {row["id"] for row in json.load(response)})
        self.assertEqual(report["window_end_offsets_days"], list(range(1, 51, 7)))
        outcomes = report["outcomes"]
        self.assertEqual(len(outcomes), 8)
        self.assertEqual([q["symbols"] for q in alpaca.bar_requests], ["AAPL", "SPY"])
        for i, outcome in enumerate(outcomes):
            self.assertEqual(outcome["status"], "complete", outcome.get("message"))
            self.assertEqual(outcome["window"], i + 1)
            if i:
                self.assertLess(outcome["end"], outcomes[i - 1]["start"])
            self.assertEqual([ref["symbol"] for ref in outcome["comparisons"]], ["AAPL", "SPY"])
            for ref in outcome["comparisons"]:
                with self.request(f"/api/results/{ref['run_id']}") as response:
                    result = json.load(response)
                spec = result["spec"]
                self.assertEqual((spec["start"], spec["end"]), (outcome["start"], outcome["end"]))
                self.assertEqual(spec["risk"]["position_fraction"], 1)
                self.assertEqual(spec["risk"]["capital"], 1000)
                self.assertEqual(spec["split"], "stock_reference")

    def test_cross_origin_blocked_even_with_token(self):
        with self.request("/api/bootstrap") as response:
            token = json.load(response)["token"]
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(
                "/api/sample", {}, {"X-Quantlab-Token": token, "Origin": "https://outside.example"}
            )
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_dns_rebinding_host_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/bootstrap", headers={"Host": "outside.example"})
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_static_traversal_and_live_order_route_absent(self):
        for path in ("/../.env", "/api/orders", "/api/live-order"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()

    def test_csv_export_matches_saved_trades(self):
        spec = {"dataset": self.store.datasets()[0]["id"], "strategy": "buy_hold"}
        run_id = self.store.create_run(spec)
        result = {"manifest": {}, "trades": [{"symbol": "SPY", "net_pnl": 5}]}
        self.store.save_result(run_id, result)
        with self.request(f"/api/results/{run_id}?format=csv") as response:
            self.assertIn("symbol,net_pnl", response.read().decode())
            self.assertIn("attachment", response.headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
