"""Offline focused checks for this local packet; no fetch, database or writer imports."""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from pypdf import PdfReader
from quantlab.completed_session import (
    CalendarSessionEvidence, CalendarSessionStatus, CompletedSessionDecision,
    CompletedSessionReason, ProviderCompletionWatermark, ProviderPublicationObservation,
    PublicationDelayPolicy, SymbolSessionEvidence, SymbolSessionStatus,
    evaluate_completed_session,
)


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


class PacketChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = json.loads((HERE / "evidence_packet.json").read_text(encoding="utf-8"))
        cls.sources = {s["source_id"]: s for s in cls.packet["sources"]}
        cls.claims = {c["id"]: c for c in cls.packet["claims"]}

    def snapshot(self, source_id):
        source = self.sources[source_id]
        path = Path(source["snapshot_path"])
        if not path.is_absolute():
            path = ROOT / path
        raw = path.read_bytes()  # Missing snapshot fails explicitly, never skips qualification.
        self.assertEqual(len(raw), source["byte_length"])
        self.assertEqual(sha256(raw).hexdigest(), source["sha256"])
        self.assertEqual(source["snapshot_kind"], "ORIGINAL_HTTP_RESPONSE_BODY")
        return raw, path

    def test_vsdc_exact_identity_and_depository_claims(self):
        raw, _ = self.snapshot("vsdc-transfer-147589")
        parser = Text()
        parser.feed(raw.decode("utf-8"))
        text = " ".join(" ".join(parser.parts).split())
        # Bind to this article rather than unrelated sidebar/footer dates.
        article = text.split("CTR: Thông báo về việc chuyển dữ liệu", 1)[1].split("Tin cùng tổ chức", 1)[0]
        security = self.claims["security_identity"]["value"]
        self.assertIn("Tên tổ chức phát hành: " + security["issuer"], article)
        self.assertIn("Mã chứng khoán: " + security["symbol"], article)
        self.assertIn("Mã ISIN: " + security["isin"], article)
        transfer = self.claims["depository_transfer"]
        self.assertIn("Sàn cũ: " + transfer["value"]["old_venue_as_printed"], article)
        self.assertIn("Sàn mới: " + transfer["value"]["new_venue"], article)
        effective = date.fromisoformat(transfer["value"]["effective_date"])
        self.assertIn("Ngày hiệu lực: " + effective.strftime("%d/%m/%Y"), article)
        self.assertIn("trên hệ thống của VSD", article)
        self.assertIn("Cập nhật ngày 27/01/2022 - 12:20:26", article)
        self.assertFalse(transfer["establishes_tradability"])
        self.assertEqual(self.packet["security"]["isin"], security["isin"])

    def test_hose_row_and_column_do_not_become_first_trading_notice(self):
        _, path = self.snapshot("hose-annual-2022")
        source = self.sources["hose-annual-2022"]
        reader = PdfReader(path)
        text = " ".join(reader.pages[source["locator"]["physical_page"] - 1].extract_text().split())
        # The fund table's adjacent 'First trading day' column is not the CTR column.
        row = re.search(r"Ngày giao dịch Listing date.*?3 CTR (.*?)4 EVF", text)
        self.assertIsNotNone(row)
        self.assertIn("Viettel Construction Joint Stock Corporation 23/2/2022", row.group(1))
        self.assertEqual(self.claims["retrospective_hose_table_date"]["value"], "2022-02-23")
        self.assertFalse(self.claims["retrospective_hose_table_date"]["establishes_first_trading_day_or_status_on_target"])
        self.assertEqual(reader.metadata["/CreationDate"], source["pdf_creation_metadata"])
        self.assertEqual(reader.metadata["/ModDate"], source["pdf_modification_metadata"])
        self.assertIsNone(source["publication_time"])
        self.assertFalse(source["metadata_is_publication_proof"])

    def test_schema_unknown_blocks_even_with_fixture_open_calendar_and_completion(self):
        p = self.packet
        mapping = dict(p["symbol_session_schema_mapping"])
        mapping["target_session"] = date.fromisoformat(mapping["target_session"])
        mapping["status"] = SymbolSessionStatus(mapping["status"])
        mapping["source_references"] = tuple(mapping["source_references"])
        symbol_session = SymbolSessionEvidence(**mapping)
        self.assertTrue(symbol_session.attributable)  # Syntax attribution only.
        self.assertEqual(symbol_session.status, SymbolSessionStatus.UNKNOWN)
        self.assertEqual((symbol_session.symbol, symbol_session.venue, symbol_session.target_session.isoformat()), (p["symbol"], p["venue"], p["session_date"]))
        hashes = sorted(s["sha256"] for s in p["sources"])
        bundle = sha256(json.dumps(hashes, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(mapping["snapshot_identity"], "sha256:" + bundle)
        refs = ("fixture://not-real-calendar-or-provider-evidence",)
        target = symbol_session.target_session
        policy = PublicationDelayPolicy("fixture-only-delay", timedelta(minutes=30), refs)
        arguments = dict(symbol="CTR", provider_identity="FIXTURE_ONLY", target_session=target,
                         symbol_session=symbol_session, observations=(), publication_delay_policy=policy)
        missing_calendar = evaluate_completed_session(calendar=None, **arguments)
        self.assertEqual(missing_calendar.decision, CompletedSessionDecision.UNRESOLVED)
        self.assertEqual(missing_calendar.reasons, (CompletedSessionReason.CALENDAR_EVIDENCE_MISSING,))
        calendar = CalendarSessionEvidence("HOSE", "fixture-only", "v1", refs[0], target,
                                           CalendarSessionStatus.OPEN_COMPLETED, refs)
        t = datetime(2022, 2, 17, 12, tzinfo=timezone.utc)
        arguments["observations"] = tuple(ProviderPublicationObservation("FIXTURE_ONLY", "CTR", target, t + delta, "a" * 64, True, refs) for delta in (timedelta(), timedelta(hours=1)))
        watermark = ProviderCompletionWatermark("FIXTURE_ONLY", target, t, refs[0], refs)
        unknown = evaluate_completed_session(calendar=calendar, completion_watermark=watermark, **arguments)
        self.assertEqual(unknown.decision, CompletedSessionDecision.UNRESOLVED)
        self.assertEqual(unknown.reasons, (CompletedSessionReason.SYMBOL_SESSION_UNKNOWN,))
        self.assertFalse(unknown.admitted)
        self.assertFalse(p["research_eligible"])
        self.assertEqual(p["operational_admission"], "UNRESOLVED")
        self.assertFalse(p["point_in_time"]["historical_availability_established"])
        self.assertFalse(p["point_in_time"]["as_of_session_admissible"])


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PacketChecks))
    report = {"scope": "one real packet; all positive calendar/provider inputs are synthetic negative controls only",
              "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "passed": result.wasSuccessful(), "packet_sha256": sha256((HERE / "evidence_packet.json").read_bytes()).hexdigest(),
              "database_access": False, "provider_fetch": False, "writer_activation": False}
    (HERE / "focused_check_results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
