"""TAR UMT-shaped SOFTWARE fixtures, not real timetable labels or accuracy."""
import json,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from backend.services.timetable_parser import parse_intranet_response
from backend.services.event_adapter import canonical_api_data
from backend.models.event import CanonicalTimetable
from backend.review.binding import AssessmentInput,bind_input
from backend.review.structured import structured_application
from backend.review.policy import assess_run
from backend.review.models import ReviewError,CorrectionSubmission,FieldCorrection,HumanEvidenceInput
from backend.review.auth import TrustedHostAuthority
from backend.review.store import ReviewStore
from backend.reconstruction.models import digest
from tests.fixtures import timetable_html

def fixture():
    html=timetable_html()
    # Distinct visible cells, source-derived IDs, no known student course/date.
    for i in range(1,6):
        start,end=i+9,i+10
        a=f"{start%12 or 12}:00 {'AM' if start<12 else 'PM'}";b=f"{end%12 or 12}:00 {'AM' if end<12 else 'PM'}"
        html=html.replace('</table>',f'<tr><td>Tue 2033-02-08</td><td><div data-rel="tooltip" title="SYNTHETIC NAME (Tutorial) SYNTHETIC PERSON (TEST ROOM, Test Block)">TEST{1100+i} (T) {a} - {b}</div></td></tr></table>')
    return canonical_api_data(parse_intranet_response(html))
def request(data):
    a=data['application'];return AssessmentInput(source_packet=CanonicalTimetable.model_validate(a['extraction']),reconstruction=a['reconstruction'],policy_profile='m5-source-review-v1')

class StructuredPolicyTests(unittest.TestCase):
    def test_source_fields_ids_revisions_evidence_survive_assessment(self):
        d=fixture();a=d['application'];self.assertEqual(len(a['policy']),6)
        old=CanonicalTimetable.model_validate(a['extraction']);current=CanonicalTimetable.model_validate(d['canonical'])
        self.assertEqual(old.source,current.source);self.assertEqual(old.evidence,current.evidence)
        self.assertEqual([e.revision for e in old.events],[e.revision for e in current.events]);self.assertEqual([e.identity for e in old.events],[e.identity for e in current.events])
        self.assertEqual(len({e.identity.event_id for e in current.events}),6)
        for e,p in zip(current.events,a['policy']):
            self.assertEqual(p['revision_digest'],digest(e.revision.model_dump(mode='json')));self.assertIn(p['decision'],('REVIEW','ABSTAIN'));self.assertFalse(p['registered_routing_eligible']);self.assertIsNone(e.revision.content.recurrence)
        self.assertTrue(all(d['decision']=='REVIEW' for d in a['policy']))
        self.assertFalse(a['calendar']['preview']['write_authorized']);self.assertTrue(all(not e['sandbox_write_permitted'] for e in a['calendar']['eligibility']['events']))
    def test_exact_binding_and_policy_recomputation(self):
        d=fixture();run=bind_input(request(d));self.assertEqual(len(assess_run(run)),6)
        self.assertEqual(run.search_status,'NOT_SEARCHED_STRUCTURED');self.assertTrue(all(r.score_provenance=='STRUCTURED_SOURCE_NO_PROBABILITY' for r in assess_run(run)))
    def test_changed_or_stale_envelope_cannot_be_ingested(self):
        d=fixture();q=request(d);bad=json.loads(q.reconstruction.model_dump_json()) if hasattr(q.reconstruction,'model_dump_json') else json.loads(json.dumps(q.reconstruction))
        bad['canonical_output']['packet']['events'][0]['revision']['revision_id']=str(uuid4())
        with self.assertRaises(ReviewError):bind_input(q.model_copy(update={'reconstruction':bad}))
    def test_registered_policy_never_enabled_by_structured_source(self):
        with self.assertRaises(ReviewError):bind_input(request(fixture()).model_copy(update={'policy_profile':'registered'}))
    def test_unsupported_or_missing_source_fields_do_not_become_good_readings(self):
        html=timetable_html(visible='TEST1001 (L) Time TBA')
        data=canonical_api_data(parse_intranet_response(html));self.assertTrue(all(p['decision']=='ABSTAIN' for p in data['application']['policy']))
    def test_forged_normalized_field_disagrees_with_source(self):
        q=request(fixture());event=q.source_packet.events[0];content=event.revision.content.model_copy(update={'location':'SYNTHETIC_FORGED'})
        bad=event.model_copy(update={'revision':event.revision.model_copy(update={'content':content})})
        packet=q.source_packet.model_copy(update={'events':(bad,*q.source_packet.events[1:])})
        with self.assertRaises(ReviewError):structured_application(packet)
    def test_authorized_synthetic_human_revision_revalidation_and_stale_rejection(self):
        q=request(fixture());s=q.source_packet.source;auth=TrustedHostAuthority(test_only_source_bindings={(s.source_document_id,s.content_sha256)})
        principal,_=auth.issue('SYNTHETIC_STRUCTURED_REVIEW',{'read','queue','assign','correct','revalidate','oversee'},mode='TEST_ONLY',source_bindings={(s.source_document_id,s.content_sha256)})
        store=ReviewStore(auth);task_id=store.ingest(q,principal)[0];t=store.get(task_id,principal);t=store.assign(task_id,principal.actor_id,t.etag,principal);t=store.start(task_id,t.etag,principal)
        span=next(e for e in store.evidence(task_id,principal) if 'cell' in e.evidence_id)
        proposal=CorrectionSubmission(request_id=uuid4(),expected_revision_id=t.current_event.revision.revision_id,expected_etag=t.etag,reason='SYNTHETIC SOFTWARE CORRECTION ONLY',human_evidence=(HumanEvidenceInput(evidence_id='human:SYNTHETIC_STRUCTURED',raw_text='SYNTHETIC ROOM',source_evidence_ids=(span.evidence_id,),note='Invented fixture assertion; no real correction'),),fields=(FieldCorrection(field_name='location',state='PRESENT',raw_value='SYNTHETIC ROOM',value='SYNTHETIC ROOM',evidence_ids=('human:SYNTHETIC_STRUCTURED',)),))
        changed=store.submit(task_id,proposal,principal);self.assertTrue(changed['applied']);t=changed['task'];self.assertEqual(t.decision.decision.value,'ABSTAIN')
        checked=store.revalidate(task_id,t.etag,principal);self.assertEqual(checked.current_event.revision.content.location,'SYNTHETIC ROOM');self.assertEqual(checked.decision.revision_digest,digest(checked.current_event.revision.model_dump(mode='json')))
        self.assertFalse(checked.decision.registered_routing_eligible);self.assertEqual(checked.current_event.human_verification.status,'NOT_VERIFIED')
        with self.assertRaises(ReviewError):store.submit(task_id,proposal.model_copy(update={'request_id':uuid4()}),principal)


    def test_structured_history_restart_requires_renewed_source_authorization(self):
        from backend.review.persistence import PersistentReviewStore,ReviewJournal
        q=request(fixture());source=q.source_packet.source;binding=(source.source_document_id,source.content_sha256)
        auth=TrustedHostAuthority(test_only_source_bindings={binding});actor='SYNTHETIC_STRUCTURED_RESTART'
        principal,_=auth.issue(actor,{'read','queue','oversee'},mode='TEST_ONLY',source_bindings={binding})
        with TemporaryDirectory(prefix='stair-structured-SYNTHETIC-') as directory:
            store=PersistentReviewStore(auth,ReviewJournal(Path(directory),actor));task_id=store.ingest(q,principal)[0];original=store.get(task_id,principal)
            other=TrustedHostAuthority(test_only_source_bindings={binding});restored=PersistentReviewStore(other,ReviewJournal(Path(directory),actor))
            with self.assertRaises(ReviewError):restored.get(task_id,principal)
            renewed,_=other.issue(actor,{'read','oversee'},mode='TEST_ONLY',source_bindings={binding})
            self.assertEqual(restored.get(task_id,renewed).current_event,original.current_event)
    def test_html_file_uses_existing_explicit_console_scope(self):
        from backend.review.local_host import LocalReviewHost
        html=timetable_html();data=canonical_api_data(parse_intranet_response(html));q=request(data);source=q.source_packet.source
        auth=TrustedHostAuthority(test_only_source_bindings={(source.source_document_id,source.content_sha256)})
        host=LocalReviewHost(ReviewStore(auth),'SYNTHETIC_HTML_OWNER',identity=lambda:'SYNTHETIC_HTML_OWNER',grant_mode='TEST_ONLY')
        with TemporaryDirectory(prefix='stair-html-SYNTHETIC-') as directory:
            original=Path(directory)/'SYNTHETIC.html';original.write_bytes(html.encode('utf-8'))
            cookie,pending=host.request_access({'source_document_id':source.source_document_id,'content_sha256':source.content_sha256})
            self.assertFalse(pending['authorized']);host.authorize_from_console(pending['nonce'],original)
            record=next(iter(host.channels.values()));self.assertEqual(record.source,(source.source_document_id,source.content_sha256));self.assertIsNotNone(record.principal)
