import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from collect_insider_transactions import filing_summaries, parse_transaction_filing
from collect_usaspending import write_json_lines
from forward_evidence_test import create_enrollments, insider_candidates, later_candidates


def tx(code, side, shares):
    return f"""<nonDerivativeTransaction><securityTitle><value>Common stock</value></securityTitle>
    <transactionDate><value>2026-11-18</value></transactionDate>
    <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
    <transactionAmounts><transactionShares><value>{shares}</value></transactionShares>
    <transactionPricePerShare><value>25.50</value></transactionPricePerShare>
    <transactionAcquiredDisposedCode><value>{side}</value></transactionAcquiredDisposedCode>
    </transactionAmounts></nonDerivativeTransaction>"""


def score(evidence_id):
    return {"score": 6, "direction": "positive", "confidence": "medium", "rationale": "Fixture",
            "evidence_ids": [evidence_id], "evidence_relations": {evidence_id: "corroborates"}}


class ForwardEvidenceTest(unittest.TestCase):
    def test_form4_keeps_both_transaction_sides_and_groups_one_disclosure(self):
        xml = ("<ownershipDocument><documentType>4</documentType><aff10b5One>1</aff10b5One>"
               "<issuer><issuerTradingSymbol>RTX</issuerTradingSymbol></issuer>"
               "<nonDerivativeTable>" + tx("P", "A", 10) + tx("S", "D", 20) + tx("A", "A", 100)
               + tx("S", "A", 100) + "</nonDerivativeTable></ownershipDocument>")
        accepted = datetime.fromisoformat("2026-11-20T19:30:00+00:00")
        observed = datetime.fromisoformat("2026-11-21T01:00:00+00:00")
        rows = list(parse_transaction_filing(
            xml.encode(), "RTX", "a1", "https://example.test/form4", accepted, observed,
        ))
        self.assertEqual([r["event_type"] for r in rows], ["insider_purchase", "insider_sale"])
        self.assertEqual(rows[0]["source_record"]["reported_value_usd"], "255.00")
        summary, = filing_summaries(rows)
        self.assertEqual(summary["source_record"]["side"], "mixed")
        self.assertTrue(summary["source_record"]["filing_indicates_10b5_1_plan"])
        self.assertIsNone(summary["published_at_utc"])
        candidate, = insider_candidates([summary], observed)
        baseline = {"score": 4, "direction": "neutral", "confidence": "medium", "rationale": "Fixture"}
        enrollment, = create_enrollments(
            {candidate["event_id"]: candidate}, set(), {candidate["event_id"]: baseline}, {},
            "insider", observed,
        )
        self.assertEqual(enrollment["entry_session"], "2026-11-23")
        self.assertEqual(enrollment["transaction_side"], "mixed")

    def test_later_confirmation_cannot_backdate_the_initial_window(self):
        original = "RTX:AB123456"
        old = {"event_id": original, "registered_at_utc": "2026-11-21T12:00:00+00:00",
               "ticker": "RTX", "contract_only": {"score": 4}, "entry_session": "2026-11-23"}
        same_award = {
            "ticker": "RTX", "event_type": "subaward", "source": "test",
            "source_id": "subaward", "source_url": "https://example.test/subaward",
            "version_sha256": "v2", "published_at_utc": None,
            "first_observed_at_utc": "2026-11-24T16:01:00+00:00",
            "source_record": {"award_id": "AB-123456", "transaction_date": "2026-11-23"},
        }
        stale = {**same_award, "version_sha256": "old",
                 "source_record": {"award_id": "AB123456", "transaction_date": "2026-11-20"}}
        now = datetime.fromisoformat("2026-11-24T17:00:00+00:00")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            write_json_lines(path / "enrollments.txt", [old])
            write_json_lines(path / "candidates.txt", [{
                "event_id": original, "summary": {"award_id": "AB123456"},
            }])
            packet, = later_candidates([same_award, stale], path, now)
            event = packet["event_id"]
            self.assertEqual([r["evidence_id"] for r in packet["company_evidence"]], ["v2"])
            result, = create_enrollments({event: packet}, set(), {}, {event: score("v2")},
                                         "followup", now)
            self.assertEqual(result["parent_event_id"], original)
            self.assertEqual(result["entry_session"], "2026-11-25")
            self.assertEqual(old["entry_session"], "2026-11-23")
