"""Focused offline checks for the updated one-session evidence packet only."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE
sys.path.insert(0, str(ROOT))
from pypdf import PdfReader
from quantlab.completed_session import (
    CalendarSessionEvidence, CalendarSessionStatus, CompletedSessionDecision,
    CompletedSessionReason, ProviderCompletionWatermark, ProviderPublicationObservation,
    PublicationDelayPolicy, SymbolSessionEvidence, SymbolSessionStatus, evaluate_completed_session,
)


def resolve_snapshot(original_path, packet):
    # Retain historical provenance paths while requiring repository-owned bytes.
    relocated = packet['checkpoint']['snapshot_paths'].get(original_path, original_path)
    path = (ROOT / relocated.replace('\\', '/')).resolve()
    if not path.is_relative_to(ROOT):
        raise AssertionError('Snapshot outside checkpoint: ' + original_path)
    return path


class NoticeChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = json.loads((HERE/'evidence_packet.json').read_text(encoding='utf-8'))
        previous = resolve_snapshot(cls.packet['review_update']['previous_packet_snapshot_path'], cls.packet)
        assert sha256(previous.read_bytes()).hexdigest() == cls.packet['review_update']['previous_packet_sha256']
        cls.old = json.loads(previous.read_text(encoding='utf-8'))
        cls.source = next(s for s in cls.packet['sources'] if s['source_id']=='hose-first-trading-191-20220215')
        cls.claims = {c['id']: c for c in cls.packet['claims']}

    def raw(self, record):
        path=resolve_snapshot(record['snapshot_path'], self.packet)
        raw=path.read_bytes()
        self.assertEqual(sha256(raw).hexdigest(), record['sha256'])
        self.assertEqual(len(raw), record['byte_length'])
        self.assertEqual(record['snapshot_kind'], 'ORIGINAL_HTTP_RESPONSE_BODY')
        return raw

    def test_original_hash_copy_and_official_retrieval_chain(self):
        for record in self.packet['checkpoint']['snapshot_inventory']:
            raw = resolve_snapshot(record['original_snapshot_path'], self.packet).read_bytes()
            self.assertEqual((len(raw), sha256(raw).hexdigest()), (record['byte_length'], record['sha256']))
        manifest=json.loads((HERE/'collection_manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['checkpoint'], self.packet['checkpoint'])
        s=self.source
        official=self.raw(s)
        self.assertEqual(official, self.raw(s['issuer_copy']))
        self.assertEqual(s['sha256'], 'a8ec7676fb47db068dc663d356d1a1eac6e4b7be17a296a6895ded74e7fb28e5')
        detail=json.loads(self.raw(s['hose_notice_api']))
        attachments=json.loads(self.raw(s['hose_attachment_api']))
        self.assertTrue(detail['success'])
        self.assertEqual((detail['data']['id'],detail['data']['code']), (1500539,'CTR'))
        self.assertEqual(attachments['data']['paging']['totalCount'], 1)
        attachment_path=attachments['data']['list'][0]['filePath']
        from urllib.parse import quote
        self.assertEqual(s['url'], 'https://staticfile.hsx.vn'+quote(attachment_path.removeprefix('~'),safe='/'))
        index=self.raw(s['issuer_disclosure_index']).decode('utf-8')
        matching=[r for r in re.findall(r'<tr\b.*?</tr>',index,re.S) if s['issuer_copy']['url'] in r]
        self.assertEqual(len(matching),1)
        self.assertIn('Thông báo v/v niêm yết và ngày giao dịch đầu tiên của CTR trên HOSE',matching[0])
        self.assertIn(s['issuer_index_displayed_publication_at'],matching[0])
        reader=PdfReader(resolve_snapshot(s['snapshot_path'], self.packet))
        self.assertEqual(len(reader.pages),1)
        self.assertEqual(reader.pages[0].extract_text().strip(),'')
        self.assertEqual(reader.metadata['/CreationDate'],s['pdf_creation_metadata'])
        self.assertIn('Manual visual review',s['verification_method'])
        self.assertFalse(s['metadata_is_publication_proof'])

    def test_claim_mapping_uses_notice_boundary_and_preserves_old_evidence(self):
        p=self.packet; s=self.source; c=self.claims['hose_first_official_trading']
        self.assertEqual(p['sources'][:len(self.old['sources'])],self.old['sources'])
        self.assertEqual(p['claims'][:len(self.old['claims'])],self.old['claims'])
        for source in self.old['sources']: self.raw(source)
        self.assertEqual((s['document_number'],s['document_issue_date']),('191/TB-SGDHCM','2022-02-15'))
        self.assertEqual(c['source_id'],s['source_id'])
        self.assertEqual(c['exact_excerpts'],['Về việc niêm yết và ngày giao dịch đầu tiên của cổ phiếu','Ngày chính thức giao dịch: 23/02/2022'])
        self.assertEqual((c['value'],s['first_official_trading_date']),('2022-02-23','2022-02-23'))
        self.assertEqual(s['listing_effective_date'],'2021-12-27')
        self.assertNotEqual(s['listing_effective_date'],c['value'])
        target=self.claims['hose_target_session_before_first_trading']
        self.assertEqual((target['symbol'],target['venue'],target['session_date']),('CTR','HOSE','2022-02-17'))
        self.assertEqual(target['depends_on_claim'],c['id'])
        self.assertLess(date.fromisoformat(target['session_date']),date.fromisoformat(c['value']))
        self.assertEqual(target['value'],'NOT_TRADING')
        self.assertIsNone(s['security_identity_in_document']['isin'])
        self.assertEqual(s['isin_binding']['source_id'],'vsdc-transfer-147589')
        self.assertEqual(p['security']['isin'],'VN000000CTR4')
        from html.parser import HTMLParser
        class Text(HTMLParser):
            def __init__(self): super().__init__(); self.parts=[]
            def handle_data(self, text): self.parts.append(text)
        vsd=next(x for x in p['sources'] if x['source_id']==s['isin_binding']['source_id'])
        parser=Text(); parser.feed(self.raw(vsd).decode('utf-8'))
        text=' '.join(' '.join(parser.parts).split())
        article=text.split('CTR: Thông báo về việc chuyển dữ liệu',1)[1].split('Tin cùng tổ chức',1)[0]
        self.assertIn('Mã ISIN: '+p['security']['isin'], article)
        self.assertIn('Mã chứng khoán: CTR', article)
        self.assertEqual(self.claims['security_identity']['source_id'], vsd['source_id'])
        self.assertEqual(p['checkpoint']['historical_availability'], 'UNVERIFIED')
        self.assertFalse(self.claims['depository_transfer']['establishes_tradability'])
        mapping=p['symbol_session_schema_mapping']
        expected_refs=[source['url']+'#sha256='+source['sha256'] for source in (p['sources'][0],s)]
        self.assertEqual(mapping['source_references'],expected_refs)  # Annual report is not a status source.
        bundle=sha256(json.dumps(sorted(x['sha256'] for x in p['sources']),separators=(',',':')).encode()).hexdigest()
        self.assertEqual(mapping['snapshot_identity'],'sha256:'+bundle)
        self.assertFalse(p['point_in_time']['historical_availability_established'])
        self.assertFalse(p['point_in_time']['as_of_session_admissible'])
        self.assertFalse(p['research_eligible'])
        self.assertFalse(p['operational_eligible'])

    def test_fail_closed_for_target_and_withdrawn_or_missing_evidence(self):
        p=self.packet; mapping=dict(p['symbol_session_schema_mapping'])
        mapping['target_session']=date.fromisoformat(mapping['target_session'])
        mapping['status']=SymbolSessionStatus(mapping['status'])
        mapping['source_references']=tuple(mapping['source_references'])
        symbol_session=SymbolSessionEvidence(**mapping)
        target=symbol_session.target_session
        refs=('fixture://negative-control-not-real-calendar-or-completion',)
        policy=PublicationDelayPolicy('fixture-only-delay',timedelta(minutes=30),refs)
        t=datetime(2022,2,17,12,tzinfo=timezone.utc)
        observations=tuple(ProviderPublicationObservation('FIXTURE_ONLY','CTR',target,t+delta,'a'*64,True,refs) for delta in (timedelta(),timedelta(hours=1)))
        watermark=ProviderCompletionWatermark('FIXTURE_ONLY',target,t,refs[0],refs)
        args=dict(symbol='CTR',provider_identity='FIXTURE_ONLY',target_session=target,observations=observations,publication_delay_policy=policy,completion_watermark=watermark)
        actual=evaluate_completed_session(calendar=None,symbol_session=symbol_session,**args)
        self.assertEqual(actual.decision,CompletedSessionDecision.UNRESOLVED)
        self.assertEqual(actual.reasons,(CompletedSessionReason.CALENDAR_EVIDENCE_MISSING,))
        calendar=CalendarSessionEvidence('HOSE','fixture-only','v1',refs[0],target,CalendarSessionStatus.OPEN_COMPLETED,refs)
        rejected=evaluate_completed_session(calendar=calendar,symbol_session=symbol_session,**args)
        self.assertEqual(rejected.decision,CompletedSessionDecision.REJECTED)
        self.assertEqual(rejected.reasons,(CompletedSessionReason.SYMBOL_NOT_TRADING,))
        withdrawn=replace(symbol_session,status=SymbolSessionStatus.UNKNOWN,source_identity='VSD_ONLY_NO_TRADING_NOTICE',source_references=(mapping['source_references'][0],))
        unknown=evaluate_completed_session(calendar=calendar,symbol_session=withdrawn,**args)
        self.assertEqual(unknown.decision,CompletedSessionDecision.UNRESOLVED)
        self.assertEqual(unknown.reasons,(CompletedSessionReason.SYMBOL_SESSION_UNKNOWN,))
        missing=evaluate_completed_session(calendar=calendar,symbol_session=None,**args)
        self.assertEqual(missing.decision,CompletedSessionDecision.UNRESOLVED)
        self.assertEqual(missing.reasons,(CompletedSessionReason.SYMBOL_SESSION_EVIDENCE_MISSING,))
        for result in (actual,rejected,unknown,missing): self.assertFalse(result.admitted)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(NoticeChecks))
    report={'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful(),
            'packet_sha256':sha256((HERE/'evidence_packet.json').read_bytes()).hexdigest(),
            'original_document_sha256':NoticeChecks.source['sha256'],
            'manual_scan_transcription':'Visually reviewed, bound to original PDF hash; assertions do not OCR or independently validate the handwriting',
            'target':'CTR/HOSE/2022-02-17','mapping_status':'NOT_TRADING',
            'checks':['Original/mirror hashes and issuer/HOSE retrieval bindings','Claim/date/identity mapping and prior-evidence preservation','Fail-closed with missing calendar, ineligible symbol, withdrawn notice or missing symbol evidence'],
            'database_access':False,'provider_fetch':False,'writer_activation':False}
    (OUT/'notice_packet_check_results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
