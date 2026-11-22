import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import forward_contract_test as study
from collect_usaspending import write_json_lines


def score(value):
    return {"score": value, "direction": "positive", "confidence": "high", "rationale": "Fixture"}


class ForwardContractTest(unittest.TestCase):
    def test_register_freezes_a_common_entry_and_exits(self):
        available = datetime.fromisoformat("2026-11-22T01:00:00+00:00")
        events = {"LMT:PILOT": {
            "version_sha256": "fixture", "contract_summary_id": "summary",
            "frozen_at_utc": "2026-11-21T20:00:00+00:00",
            "summary": {"ticker": "LMT", "evidence": [{
                "event_date": "2026-11-20", "posted_date": "2026-11-20",
                "first_observed_date_utc": "2026-11-21",
            }]},
            "company_evidence": [{"evidence_id": "evidence", "award_id_mentioned": False}],
        }}
        base = {"LMT:PILOT": score(7)}
        context = {"LMT:PILOT": {**score(9), "evidence_ids": ["evidence"]}}
        rows = study.enroll(events, set(), base, context, available)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_session"], "2026-11-23")
        self.assertEqual(rows[0]["exit_sessions"]["5"], "2026-11-30")
        self.assertEqual(rows[0]["context_type"], "company_context")
        self.assertEqual(study.enroll(events, {"LMT:PILOT"}, base, context, available), [])
        with self.assertRaisesRegex(ValueError, "does not match"):
            study.enroll(events, set(), base, {
                "LMT:PILOT": {**score(9), "evidence_ids": ["unfrozen-evidence"]},
            }, available)
        after_open = datetime.fromisoformat("2026-11-23T14:30:00+00:00")
        self.assertEqual(study.entry_window(after_open)[0], "2026-11-24")

    def test_measure_waits_for_the_close_and_compares_the_same_event(self):
        entry = datetime.fromisoformat("2026-11-22T01:00:00+00:00")
        day, open_time, exits = study.entry_window(entry)
        event = {
            "event_id": "LMT:PILOT", "ticker": "LMT", "entry_session": day,
            "entry_at_utc": open_time, "exit_sessions": exits, "contract_only": score(7),
            "with_other_sources": score(9), "context_type": "company_context",
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            write_json_lines(directory / "enrollments.txt", [event])
            with patch.object(study, "Alpaca") as provider:
                study.measure(directory, datetime.fromisoformat("2026-11-23T21:15:00+00:00"))
                provider.assert_not_called()
                provider.return_value.history.return_value = [
                    SimpleNamespace(symbol="LMT", session=day, open=100, close=108),
                    SimpleNamespace(symbol="ITA", session=day, open=50, close=51),
                ]
                now = datetime.fromisoformat("2026-11-23T21:16:00+00:00")
                study.measure(directory, now)
                study.measure(directory, now)
                provider.return_value.history.assert_called_once()
            returns = list(study.read_lines(directory / "returns.txt"))
            self.assertEqual(len(returns), 1)
            self.assertEqual(returns[0]["excess_return_pct"], 6.0)
            day_one, day_five, day_twenty = study.read_lines(directory / "comparison.txt")
            self.assertEqual(day_one["paired_contract_only"]["buy_count"], 0)
            self.assertEqual(day_one["paired_with_other_sources"]["buy_mean_excess_pct"], 6.0)
            self.assertEqual(day_one["paired_decisions_changed"], 1)
            self.assertEqual(day_five["pending"], 1)
            self.assertEqual(day_twenty["pending"], 1)
            with patch.object(study, "Alpaca") as provider:
                provider.return_value.history.return_value = [
                    SimpleNamespace(symbol=symbol, session=session, open=100, close=close)
                    for symbol, close in (("LMT", 110), ("ITA", 105))
                    for session in exits.values()
                ]
                study.measure(directory, datetime.fromisoformat("2026-12-22T01:00:00+00:00"))
            measured = list(study.read_lines(directory / "returns.txt"))
            self.assertEqual(sorted(r["horizon_sessions"] for r in measured), [1, 5, 20])
            self.assertEqual(measured[1]["excess_return_pct"], 5.0)
            self.assertEqual(measured[2]["excess_return_pct"], 5.0)

    def test_context_excludes_post_freeze_evidence(self):
        now = datetime.fromisoformat("2026-11-22T01:00:00+00:00")
        in_time = {
            "ticker": "LMT", "event_type": "business_award", "source": "issuer_news",
            "first_observed_at_utc": "2026-11-21T00:00:00+00:00",
            "published_at_utc": "2026-11-20T00:00:00+00:00", "source_id": "one",
            "version_sha256": "v1", "source_url": "https://example.com/one",
            "source_record": {"headline": "Award ABC-123456", "brief": ""},
        }
        later = {**in_time, "version_sha256": "v2", "first_observed_at_utc": "2026-11-23T00:00:00Z"}
        rows = study.eligible_context([in_time, later], "LMT", "ABC123456", now)
        self.assertEqual([r["evidence_id"] for r in rows], ["v1"])
        self.assertTrue(rows[0]["award_id_mentioned"])

    def test_paired_insider_effect_uses_same_contract_and_company_evidence(self):
        now = datetime.fromisoformat("2026-11-22T01:00:00+00:00")
        event = "LMT:PILOT"
        packet = {
            "version_sha256": "fixture", "contract_summary_id": "summary",
            "frozen_at_utc": now.isoformat(),
            "summary": {"ticker": "LMT", "evidence": [{
                "event_date": "2026-11-20", "posted_date": "2026-11-20",
                "first_observed_date_utc": "2026-11-21",
            }]},
            "company_evidence": [{"evidence_id": "issuer", "award_id_mentioned": False}],
            "insider_evidence": [{"evidence_id": "form4", "award_id_mentioned": False}],
        }
        baseline = {event: score(5)}
        with_insider = {event: {**score(8), "evidence_ids": ["issuer", "form4"]}}
        without_insider = {event: {**score(7), "evidence_ids": ["issuer"]}}
        tracked = study.enroll({event: packet}, set(), baseline, with_insider, now, without_insider)
        with self.assertRaisesRegex(ValueError, "does not match"):
            study.enroll({event: packet}, set(), baseline, with_insider, now, with_insider)
        day_one = next(study.compare(tracked, [{
            "event_id": event, "horizon_sessions": 1, "excess_return_pct": 1.25,
        }]))["incremental_insider"]
        self.assertEqual(day_one["registered_decisions_changed"], 1)
        self.assertEqual(day_one["mean_score_change"], 1)
        self.assertEqual(day_one["without_insider"]["buy_count"], 0)
        self.assertEqual(day_one["with_insider"]["buy_mean_excess_pct"], 1.25)
