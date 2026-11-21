"""Invented context approvals and mocked receipts; no actual human approval."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.student.calendar_context import ApprovedContextFile
from backend.student.registered_calendar import bind_registered_rows
from backend.student.workflow import Schedule, occurrences
from backend.reconstruction.models import digest
from backend.review.models import ReviewError
import test_student_registered_calendar as registered_tests
from test_m7_analysis import make


class CalendarContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'context.json'
        self.helper=registered_tests.RegisteredCalendarBridgeTests('test_current_receipt_and_source_backed_dates_are_bound_to_rows')
        self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.q=make([{'recurrence':{'frequency':'WEEKLY','interval':1,'term_start':'2026-01-05','term_end':'2026-01-25',
            'excluded_dates':['2026-01-12'],'replacement_dates':['2026-01-13']},'teaching_weeks':[1,2,3]}],
            term=('2026-01-05','2026-01-25'),weeks=((1,'2026-01-05','2026-01-11'),(2,'2026-01-12','2026-01-18'),(3,'2026-01-19','2026-01-25')))
        event=self.q.packet.events[0];h=self.helper
        decision=h.task.decision.model_copy(update={'candidate_id':event.candidate_id,'event_id':event.identity.event_id,
            'revision_id':event.revision.revision_id,'revision_digest':digest(event.revision.model_dump(mode='json'))})
        h.task=h.task.model_copy(update={'current_event':event,'decision':decision})
        h.snapshot={'packet':self.q.packet.model_dump(mode='json'),'tasks':[h.task.model_dump(mode='json')],
                    'reconstruction_status':'COMPLETE_FINITE_SEARCH','reconstruction_context':{}}
        self.record={'source_document_id':self.q.packet.source.source_document_id,'source_sha256':self.q.packet.source.content_sha256,
            'evidence_digest':digest([s.model_dump(mode='json') for s in self.q.packet.evidence]),'context':self.q.context.model_dump(mode='json')}
        self.data={'schema_version':'approved-calendar-context-v1','origin':'AUTHENTIC','approval_record_id':'SYNTHETIC-ONLY',
            'approved_by':'SYNTHETIC SOFTWARE TEST','approved_at':'2026-01-01T00:00:00Z','records':[self.record]}
        self.schedule=Schedule(task_id=str(h.task.task_id),revision_id=str(event.revision.revision_id),frequency='WEEKLY',
            first_date='2026-01-05',last_date='2026-01-25',timezone='Etc/UTC',checked_original=True)
        self.rows=occurrences(h.task,self.schedule,h.session.user_id)

    def load(self):
        self.path.write_text(json.dumps(self.data));return ApprovedContextFile(self.path,sha256(self.path.read_bytes()).hexdigest())

    def bind(self,context,schedule=None):
        h=self.helper
        return bind_registered_rows(h.registry,h.store,h.session,h.task,self.rows,context_provider=context,schedule=schedule or self.schedule)

    def test_source_weeks_holiday_and_replacement_determine_actual_output(self):
        context=self.load();result=self.bind(context)
        self.assertEqual([r['body']['start']['dateTime'][:10] for r in result],['2026-01-05','2026-01-13','2026-01-19'])
        self.assertEqual(result,self.bind(context))
        self.assertEqual(self.q.packet.model_dump(mode='json'),self.helper.snapshot['packet'])

    def test_range_is_subset_and_user_cannot_extend_source_term(self):
        context=self.load();short=self.schedule.model_copy(update={'last_date':self.schedule.first_date})
        self.assertEqual(len(self.bind(context,short)),1)
        from datetime import date
        with self.assertRaises(ReviewError):self.bind(context,self.schedule.model_copy(update={'last_date':date(2026,2,1)}))

    def test_file_tampering_and_wrong_approval_hash_are_blocked(self):
        context=self.load();self.path.write_text('{}')
        with self.assertRaises(ReviewError):context.context_for(self.q.packet)
        with self.assertRaises(ValueError):ApprovedContextFile(self.path,'0'*64)

    def test_wrong_source_or_changed_evidence_does_not_inherit_context(self):
        context=self.load();packet=self.q.packet
        for changed in (packet.model_copy(update={'source':packet.source.model_copy(update={'content_sha256':'0'*64})}),
                        packet.model_copy(update={'evidence':packet.evidence[:-1]})):
            with self.assertRaises(ReviewError):context.context_for(changed)

    def test_unknown_or_non_authority_references_are_blocked(self):
        self.record['context']['timezone_evidence_ids']=['not-existing']
        with self.assertRaises(ReviewError):self.load().context_for(self.q.packet)
        self.record['context']['timezone_evidence_ids']=[self.q.packet.evidence[0].evidence_id]
        with self.assertRaises(ReviewError):self.load().context_for(self.q.packet)

    def test_synthetic_origin_and_duplicate_bindings_are_rejected(self):
        self.data['origin']='SYNTHETIC'
        with self.assertRaises(ValueError):self.load()
        self.data['origin']='AUTHENTIC';self.data['records'].append(deepcopy(self.record))
        with self.assertRaises(ValueError):self.load()
