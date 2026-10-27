"""Thread-safe application repair queue with append-only histories and revision checks.

Development storage is process-local. It is intentionally not the research gold
workflow or a database migration. All externally returned records are detached.
"""
from dataclasses import dataclass, field
from threading import RLock
from uuid import uuid4
import json
from pydantic import ValidationError

from backend.models.event import CanonicalTimetable, PolicyDecision, HumanReview, ConstraintValidation
from backend.reconstruction.models import canonical_bytes, digest
from backend.review.auth import utc_now
from backend.review.binding import bind_input
from backend.review.corrections import prepare_correction
from backend.review.models import (AuditEntry, CorrectionRecord, ReviewTask, ReviewState, ReviewError)
from backend.review.policy import assess_run, assess_event
from backend.review.validation import validation_view, assessment_for


@dataclass
class _Task:
    view: ReviewTask
    audits: tuple[AuditEntry,...] = ()
    corrections: tuple[CorrectionRecord,...] = ()
    revisions: tuple[str,...] = ()
    decisions: tuple[str,...] = ()
    assessments: tuple[str,...] = ()


@dataclass
class _Run:
    snapshot: object
    evidence: tuple
    context: object
    events: dict = field(default_factory=dict)
    requests: set = field(default_factory=set)


class ReviewStore:
    def __init__(self,authority,clock=utc_now):
        self.authority,self.clock=authority,clock
        self._tasks={}; self._runs={}; self._lock=RLock()

    def _task(self,task_id,principal,permission):
        self.authority.require(principal,permission)
        self.authority.require(principal,'read')
        task=self._tasks.get(str(task_id))
        if task is None: raise ReviewError('REVIEW_TASK_NOT_FOUND',404)
        self.authority.require_source(principal,self._source(task.view.run_id))
        if 'oversee' not in principal.permissions and task.view.assignee!=principal.actor_id:
            raise ReviewError('TASK_ACCESS_DENIED',403)
        return task

    def _etag(self,task,expected):
        if task.view.etag!=expected: raise ReviewError('STALE_REVIEW_TASK',409)

    @staticmethod
    def _with_decision(event,decision):
        return event.model_copy(update={'policy':PolicyDecision(decision=decision.decision,revision_id=event.revision.revision_id,
            policy_version=decision.policy_profile+':'+decision.policy_version,reason_codes=decision.reason_codes)}) if event else None

    def _record(self,task,operation,principal,new_state,reason,payload=None):
        now=self.clock()
        values={'sequence':len(task.audits)+1,'operation':operation,'actor_id':principal.actor_id,'recorded_at':now.isoformat(),
                'from_state':task.view.state.value,'to_state':new_state.value,'reason':reason,
                'previous_hash':task.audits[-1].entry_hash if task.audits else None,
                'payload_json':canonical_bytes(payload or {}).decode()}
        entry=AuditEntry.model_validate({**values,'entry_hash':'0'*64})
        # Hash the serialized contract (including Pydantic's UTC timestamp format).
        entry=entry.model_copy(update={'entry_hash':digest(entry.model_dump(mode='json',exclude={'entry_hash'}))})
        task.audits=task.audits+(entry,)
        task.view=task.view.model_copy(update={'state':new_state,'etag':task.view.etag+1,'updated_at':now})

    def ingest(self,request,principal):
        self.authority.require(principal,'queue')
        self.authority.require_source(principal,request.source_packet.source)
        snapshot=bind_input(request)
        run_id=digest({'input':snapshot.input_digest,'reconstruction':snapshot.reconstruction_digest})
        with self._lock:
            if run_id in self._runs: raise ReviewError('RECONSTRUCTION_ALREADY_QUEUED',409)
            graph=snapshot.graph()
            run=_Run(snapshot,graph.evidence if graph else (),graph.context if graph else None,
                     {e.candidate_id:e for e in snapshot.events()})
            decisions=assess_run(snapshot,clock=self.clock)
            tasks=[]
            for decision in decisions:
                event=run.events.get(decision.candidate_id)
                now=self.clock()
                view=ReviewTask(task_id=uuid4(),run_id=run_id,state=ReviewState.PENDING,etag=1,assignee=None,
                    original_prediction_digest=digest(event.model_dump(mode='json')) if event else None,current_event=self._with_decision(event,decision),
                    decision=decision,created_by=principal.actor_id,created_at=now,updated_at=now,validation_dependency_digest=None)
                task=_Task(view,revisions=(event.model_dump_json(),) if event else (),decisions=(decision.model_dump_json(),))
                if event: run.events[event.candidate_id]=view.current_event
                self._record(task,'CREATE_APPLICATION_REPAIR_TASK',principal,ReviewState.PENDING,'Explicit trusted-host application repair queue',
                             {'input_digest':snapshot.input_digest,'reconstruction_digest':snapshot.reconstruction_digest})
                self._tasks[str(view.task_id)]=task; tasks.append(str(view.task_id))
            self._runs[run_id]=run
            return tuple(tasks)

    def get(self,task_id,principal):
        with self._lock:
            return ReviewTask.model_validate_json(self._task(task_id,principal,'read').view.model_dump_json())

    def _source(self,run_id):
        return CanonicalTimetable.model_validate_json(self._runs[run_id].snapshot.source_json).source

    def _visible(self,view,principal):
        return self.authority.allows_source(principal,self._source(view.run_id)) and (
            'oversee' in principal.permissions or view.assignee==principal.actor_id)

    def queue_summary(self,principal,offset=0,limit=50):
        self.authority.require(principal,'read')
        with self._lock:
            views=[t.view for t in self._tasks.values() if self._visible(t.view,principal)]
            views.sort(key=lambda t:(t.created_at,str(t.task_id)))
            return {'total':len(views),'states':{s.value:sum(t.state==s for t in views) for s in ReviewState},
                'tasks':[v.model_dump(mode='json') for v in views[offset:offset+limit]],
                'next_offset':offset+limit if offset+limit<len(views) else None,'storage':'PROCESS_LOCAL_NOT_RESTART_PERSISTENT'}

    def assign(self,task_id,assignee,expected_etag,principal):
        with self._lock:
            task=self._task(task_id,principal,'assign'); self._etag(task,expected_etag)
            if task.view.state not in {ReviewState.PENDING,ReviewState.ASSIGNED,ReviewState.ESCALATED}:
                raise ReviewError('INVALID_REVIEW_TRANSITION',409)
            if not self.authority.actor_is_authorized(assignee,'correct',self._source(task.view.run_id)):
                raise ReviewError('ASSIGNEE_NOT_AUTHORIZED',403)
            task.view=task.view.model_copy(update={'assignee':assignee})
            self._record(task,'ASSIGN',principal,ReviewState.ASSIGNED,'Host-authorized reviewer assignment',{'assignee':assignee})
            return self.get(task_id,principal)

    def start(self,task_id,expected_etag,principal):
        with self._lock:
            task=self._task(task_id,principal,'correct'); self._etag(task,expected_etag)
            if task.view.state!=ReviewState.ASSIGNED or task.view.assignee!=principal.actor_id:
                raise ReviewError('ASSIGNMENT_REQUIRED',409)
            self._record(task,'START',principal,ReviewState.IN_PROGRESS,'Assigned actor began review')
            return self.get(task_id,principal)

    def resume(self,task_id,expected_etag,principal):
        with self._lock:
            task=self._task(task_id,principal,'correct');self._etag(task,expected_etag)
            if task.view.state not in {ReviewState.REVALIDATED,ReviewState.REVALIDATION_REQUIRED} or task.view.assignee!=principal.actor_id:
                raise ReviewError('ASSIGNMENT_REQUIRED',409)
            self._record(task,'RESUME_APPLICATION_REVIEW',principal,ReviewState.IN_PROGRESS,'Assigned actor resumed bounded application repair')
            return self.get(task_id,principal)

    def _dependency(self,run):
        return digest({'events':[e.revision.model_dump(mode='json') for _,e in sorted(run.events.items())],
                       'evidence':[s.model_dump(mode='json') for s in run.evidence],'context':run.context.model_dump(mode='json')})

    def submit(self,task_id,submission,principal):
        # Round-trip protects private history from caller mutation of Any values.
        submission=type(submission).model_validate_json(submission.model_dump_json())
        with self._lock:
            task=self._task(task_id,principal,'correct'); self._etag(task,submission.expected_etag)
            event=task.view.current_event
            if event is None: raise ReviewError('NO_CORRECTABLE_CANDIDATE',409)
            run=self._runs[task.view.run_id]
            if str(submission.request_id) in run.requests: raise ReviewError('CORRECTION_REPLAY',409)
            if submission.expected_revision_id!=event.revision.revision_id: raise ReviewError('STALE_EVENT_REVISION',409)
            if task.view.state!=ReviewState.IN_PROGRESS or task.view.assignee!=principal.actor_id:
                raise ReviewError('ASSIGNMENT_REQUIRED',409)
            now=self.clock()
            source=CanonicalTimetable.model_validate_json(run.snapshot.source_json).source
            try:
                updated,evidence,context=prepare_correction(event,submission,run.evidence,source,run.context,principal.actor_id,now)
            except (ValidationError,ReviewError,ValueError) as exc:
                code=exc.code if isinstance(exc,ReviewError) else 'CORRECTION_SCHEMA_INVALID'
                record=CorrectionRecord(request_id=submission.request_id,actor_id=principal.actor_id,recorded_at=now,reason=submission.reason,
                    status='INVALID',previous_revision_id=event.revision.revision_id,new_revision_id=None,
                    before_json=event.model_dump_json(),proposal_json=submission.model_dump_json(),after_json=None,error_codes=(code,))
                run.requests.add(str(submission.request_id)); task.corrections+=(record,)
                decision=assess_event(run.snapshot,event,clock=self.clock,revalidation_required=True,invalid_correction=True)
                task.decisions+=(decision.model_dump_json(),)
                task.view=task.view.model_copy(update={'current_event':self._with_decision(event,decision),'decision':decision,'validation_dependency_digest':None})
                run.events[event.candidate_id]=task.view.current_event
                self._record(task,'REJECT_INVALID_CORRECTION',principal,ReviewState.ESCALATED,submission.reason,{'request_id':str(submission.request_id),'error':code})
                return {'applied':False,'error_code':code,'task':self.get(task_id,principal)}
            run.requests.add(str(submission.request_id))
            run.events[event.candidate_id]=updated; run.evidence=evidence; run.context=context
            record=CorrectionRecord(request_id=submission.request_id,actor_id=principal.actor_id,recorded_at=now,reason=submission.reason,
                status='SUBMITTED',previous_revision_id=event.revision.revision_id,new_revision_id=updated.revision.revision_id,
                before_json=event.model_dump_json(),proposal_json=submission.model_dump_json(),after_json=updated.model_dump_json())
            task.corrections+=(record,); task.revisions+=(updated.model_dump_json(),)
            decision=assess_event(run.snapshot,updated,clock=self.clock,revalidation_required=True)
            task.decisions+=(decision.model_dump_json(),)
            updated=self._with_decision(updated,decision)
            run.events[event.candidate_id]=updated
            task.view=task.view.model_copy(update={'current_event':updated,'decision':decision,'validation_dependency_digest':None})
            self._record(task,'SUBMIT',principal,ReviewState.SUBMITTED,submission.reason,{'before_revision':str(event.revision.revision_id),'after_revision':str(updated.revision.revision_id),'request_id':str(submission.request_id)})
            self._record(task,'REQUIRE_REVALIDATION',principal,ReviewState.REVALIDATION_REQUIRED,'New content has no current validation')
            # Sibling assessments may depend on changed time/cohort/context/evidence.
            for other in self._tasks.values():
                if other is not task and other.view.run_id==task.view.run_id and other.view.state!=ReviewState.CLOSED and other.view.current_event is not None:
                    current=other.view.current_event
                    invalidated=assess_event(run.snapshot,current,clock=self.clock,revalidation_required=True)
                    other.decisions+=(invalidated.model_dump_json(),)
                    invalid_event=current.model_copy(update={'validation':ConstraintValidation(),
                        'policy':PolicyDecision(decision='ABSTAIN',revision_id=current.revision.revision_id,
                            policy_version=invalidated.policy_profile+':'+invalidated.policy_version,reason_codes=invalidated.reason_codes)})
                    run.events[current.candidate_id]=invalid_event
                    other.view=other.view.model_copy(update={'current_event':invalid_event,'decision':invalidated,'validation_dependency_digest':None})
                    next_state=ReviewState.REVALIDATION_REQUIRED if other.view.state in {ReviewState.REVALIDATED,ReviewState.RESOLVED} else other.view.state
                    self._record(other,'INVALIDATE_DEPENDENT_REVIEW',principal,next_state,'A sibling correction changed validation dependencies')
            return {'applied':True,'task':self.get(task_id,principal)}

    def revalidate(self,task_id,expected_etag,principal):
        with self._lock:
            task=self._task(task_id,principal,'revalidate'); self._etag(task,expected_etag)
            if task.view.state not in {ReviewState.REVALIDATION_REQUIRED,ReviewState.IN_PROGRESS} or task.view.current_event is None:
                raise ReviewError('INVALID_REVIEW_TRANSITION',409)
            run=self._runs[task.view.run_id]
            _,findings=validation_view(run.snapshot,tuple(run.events.values()),run.evidence,run.context)
            event=task.view.current_event; group=dict(run.snapshot.candidate_groups)[event.candidate_id]
            validation=assessment_for(event,group,findings)
            assessed=event.model_copy(update={'validation':validation})
            dependency=self._dependency(run)
            decision=assess_event(run.snapshot,assessed,findings,clock=self.clock,validation_dependency_digest=dependency)
            assessed=self._with_decision(assessed,decision)
            run.events[event.candidate_id]=assessed
            task.assessments+=(assessed.model_dump_json(),)
            task.decisions+=(decision.model_dump_json(),)
            task.view=task.view.model_copy(update={'current_event':assessed,'decision':decision,'validation_dependency_digest':self._dependency(run)})
            self._record(task,'REVALIDATE',principal,ReviewState.REVALIDATED,'Source/field and timetable constraints reassessed',
                         {'status':validation.status.value,'decision':decision.decision.value,'revision_id':str(event.revision.revision_id),
                          'validation_dependency_digest':dependency,'assessment_input_digest':decision.assessment_input_digest})
            if validation.status.value=='FAILED':
                self._record(task,'ESCALATE_INVALID_REVISION',principal,ReviewState.ESCALATED,'Corrected revision still violates a hard constraint')
            return self.get(task_id,principal)

    def finish(self,task_id,expected_etag,principal,*,action,reason):
        if not reason or not reason.strip(): raise ReviewError('REVIEW_REASON_REQUIRED',422)
        with self._lock:
            task=self._task(task_id,principal,'resolve'); self._etag(task,expected_etag)
            if task.view.state in {ReviewState.CLOSED,ReviewState.RESOLVED}: raise ReviewError('INVALID_REVIEW_TRANSITION',409)
            if action=='resolve':
                run=self._runs[task.view.run_id]
                if task.view.state!=ReviewState.REVALIDATED or task.view.validation_dependency_digest!=self._dependency(run):
                    raise ReviewError('REVALIDATION_REQUIRED',409)
                if any(f.severity=='HARD' and f.passed is not True for f in task.view.decision.findings):
                    raise ReviewError('UNRESOLVED_HARD_CONSTRAINT',409)
                target=ReviewState.RESOLVED
                current=task.view.current_event
                reviewed=current.model_copy(update={'human_review':HumanReview(status='APPROVED',revision_id=current.revision.revision_id,
                    actor_id=principal.actor_id,recorded_at=self.clock(),evidence_ids=current.human_review.evidence_ids)})
                task.view=task.view.model_copy(update={'current_event':reviewed})
                run.events[current.candidate_id]=reviewed
            elif action=='escalate': target=ReviewState.ESCALATED
            elif action=='close': target=ReviewState.CLOSED
            else: raise ReviewError('INVALID_REVIEW_TRANSITION',409)
            self._record(task,action.upper(),principal,target,reason)
            return self.get(task_id,principal)

    def history(self,task_id,principal):
        with self._lock:
            task=self._task(task_id,principal,'read')
            return {'audit':[e.model_dump(mode='json') for e in task.audits],
                    'corrections':[c.model_dump(mode='json') for c in task.corrections],
                    'revisions':[json.loads(e) for e in task.revisions],
                    'assessments':[json.loads(e) for e in task.assessments],
                    'decisions':[json.loads(d) for d in task.decisions]}

    def evidence(self,task_id,principal):
        with self._lock:
            task=self._task(task_id,principal,'read')
            return tuple(self._runs[task.view.run_id].evidence)

    def original_reconstruction(self,task_id,principal):
        with self._lock:
            task=self._task(task_id,principal,'read')
            return self._runs[task.view.run_id].snapshot.original_json

    def source(self,task_id,principal):
        with self._lock:
            task=self._task(task_id,principal,'read')
            return self._source(task.view.run_id)

    def snapshot(self,task_id,principal):
        """Current visible revisions only; never widen assignment or imply completeness."""
        with self._lock:
            task=self._task(task_id,principal,'read');run=self._runs[task.view.run_id]
            visible=[t.view for t in self._tasks.values() if t.view.run_id==task.view.run_id and self._visible(t.view,principal)]
            events=tuple(v.current_event for v in visible if v.current_event is not None)
            packet=CanonicalTimetable(source=self._source(task.view.run_id),evidence=run.evidence,events=events)
            return {'packet':packet.model_dump(mode='json'),'reconstruction_context':run.context.model_dump(mode='json') if run.context else {},
                'tasks':[v.model_dump(mode='json') for v in visible],
                'scope':'AUTHORIZED_VISIBLE_TASKS_ONLY','complete_schedule_verified':False,
                'dependency_digest':self._dependency(run) if run.context else None,'storage':'PROCESS_LOCAL_NOT_RESTART_PERSISTENT'}
