import io
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import score_contract_events as pipeline


def exercise_pipeline(tmp_path):
    day = datetime.now(UTC).date()
    old = (day - timedelta(days=120)).isoformat()
    observed = datetime.now(UTC).isoformat()
    sam = tmp_path / "sam.txt"
    spending = tmp_path / "usaspending"
    spending.mkdir()
    output = tmp_path / "output"
    notice = {
        "source_id": "notice-1", "source_url": "https://example.test/notice-1",
        "award_date": day.isoformat(), "posted_at_source": day.isoformat(),
        "first_observed_at_utc": observed, "version_sha256": "sam-v1",
        "source_record": {
            "Awardee": "LOCKHEED MARTIN CORP", "AwardNumber": "PILOT-1",
            "Award$": "5000000", "Title": "Synthetic award",
            "Description": "raw document that must stay local",
        },
    }
    sam.write_text(json.dumps(notice) + "\n", encoding="utf-8")

    def obligation(award, action_day, version):
        return {
            "transaction_key": [award, "0"], "source_url": "https://example.test/award",
            "action_date": action_day, "first_observed_at_utc": observed, "version_sha256": version,
            "source_record": {
                "Recipient Name": "LOCKHEED MARTIN CORPORATION", "Award ID": award,
                "Transaction Amount": 4000000, "Mod": "0",
            },
        }

    transactions = [obligation("PILOT1", day.isoformat(), "us-v1"), obligation("OLD1", old, "old-v1")]
    path = spending / "transactions.txt"
    path.write_text("".join(json.dumps(row) + "\n" for row in transactions), encoding="utf-8")
    calls = []

    def respond(request, timeout):
        body = json.loads(request.data)
        assert timeout > 0 and body["store"] is False
        assert body["text"]["format"]["strict"] is True
        assert "raw document" not in body["input"] and "example.test" not in body["input"]
        calls.append(json.loads(body["input"]))
        value = {"score": 9, "direction": "positive", "confidence": "high", "rationale": "Fixture."}
        response = {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]},
        ]}
        return io.BytesIO(json.dumps(response).encode())

    argv = [
        "score_contract_events.py", "--sam", str(sam), "--usaspending", str(spending),
        "--output-dir", str(output), "--model", "test-model", "--include-historical",
    ]
    with (
        patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        patch.object(sys, "argv", argv),
        patch.object(pipeline, "urlopen", side_effect=respond),
    ):
        pipeline.main()
        first_scores = (output / "scores.txt").read_bytes()
        first_signals = (output / "paper_buy_signals.txt").read_bytes()
        pipeline.main()
        assert (output / "scores.txt").read_bytes() == first_scores
        assert (output / "paper_buy_signals.txt").read_bytes() == first_signals
        assert len(calls) == 2
        assert {row["source"] for row in calls[0]["evidence"]} == {"sam", "usaspending"}
        assert json.loads(first_signals)["event_id"] == "LMT:PILOT1"

        # New evidence is scored, but the same contract cannot emit a second buy.
        notice["version_sha256"] = "sam-v2"
        notice["source_record"]["Award$"] = "6000000"
        sam.write_text(json.dumps(notice) + "\n", encoding="utf-8")
        pipeline.main()
        assert len(calls) == 3
        assert (output / "paper_buy_signals.txt").read_bytes() == first_signals
        assert len((output / "scores.txt").read_text().splitlines()) == 3


class ContractSignalsTest(unittest.TestCase):
    def test_scoring_and_signals_end_to_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            exercise_pipeline(Path(temporary))
