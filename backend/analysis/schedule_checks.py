"""Protected source-first inventory, separate from Gold and calendar permission."""
from datetime import datetime
from threading import RLock
from uuid import uuid4
from pydantic import Field
from backend.models.evidence import ContractModel,EvidenceSpan
from backend.models.event import CanonicalTimetable
from backend.analysis.models import AnalysisInput,Scope,Availability
from backend.analysis.authority import CompletenessAuthority
from backend.analysis.student import student_analyze
from backend.calendar.models import CalendarContext
from backend.calendar.mapping import normalize_occurrences,_local
from backend.reconstruction.models import digest
from backend.review.auth import utc_now
from backend.review.models import ReviewError


class LocalAvailability(ContractModel):
    start:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')
    end:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$')


class CheckStart(ContractModel):
    task_id:str=Field(max_length=80)
    scope:Scope


class InventoryRow(ContractModel):
    decision:str=Field(pattern=r'^(INCLUDE|OMITTED|UNCERTAIN|NON_SESSION)$')
    candidate_id:str|None=Field(default=None,max_length=200)
    page:int=Field(ge=1,le=100)
    source_session:str=Field(min_length=1,max_length=200)
    evidence_ids:tuple[str,...]=Field(default=(),max_length=30)
    note:str=Field(default='',max_length=2000)


class InventorySubmission(ContractModel):
    expected_version:int=Field(ge=1)
    checked_pages:tuple[int,...]=Field(max_length=100)
    rows:tuple[InventoryRow,...]=Field(max_length=400)
    source_confirmed:bool
    scope_confirmed:bool
    note:str=Field(min_length=1,max_length=2000)


class VerificationSubmission(ContractModel):
    expected_version:int=Field(ge=1)
    source_confirmed:bool
    inventory_confirmed:bool
    temporal_context_confirmed:bool
    note:str=Field(min_length=1,max_length=2000)


class CheckAnalysis(ContractModel):
    availability:tuple[LocalAvailability,...]=Field(default=(),max_length=100)


class CheckGrounded(CheckAnalysis):
    allow_provider:bool=False


class ScheduleChecks:
    def __init__(self,store,journal_factory,manifest_lookup):
        self.store,self.journal_factory,self.manifest_lookup=store,journal_factory,manifest_lookup
        self.authority=CompletenessAuthority();self.verifiers={};self.tokens={};self.lock=RLock();self.journal=None

    def enrol_independent(self,principal,*,independent_of,expires_at):
        """Trusted identity-provider bootstrap only. Never an HTTP or owner-console role switch."""
        self.store.authority.require(principal,'read')
        self.store.authority.require(principal,'oversee')
        if not principal.source_bindings or principal.actor_id in independent_of:
            raise ReviewError('INDEPENDENT_ROLE_REQUIRED',403)
        verifier=self.authority.install_verifier(principal.actor_id,independent_of=tuple(independent_of),expires_at=min(expires_at,principal.expires_at))
        self.verifiers[principal.grant_id]=(verifier,principal,frozenset(independent_of))

    def _transaction(self):
        # Initialize once under the same process lock as operations. Recreating
        # the journal during another request's writer lock can falsely report
        # STORAGE_BUSY. Every transaction still validates and locks its paths.
        with self.lock:
            if self.journal is None:self.journal=self.journal_factory()
            return self.journal

    def _current(self,task_id,principal):
        snap=self.store.snapshot(task_id,principal)
        packet=CanonicalTimetable.model_validate(snap['packet'])
        self.store.authority.require_source(principal,packet.source)
        # Partial assignment visibility cannot be relabelled whole-schedule scope.
        self.store.authority.require(principal,'oversee')
        manifest=self.manifest_lookup(packet.source)
        if not manifest or manifest['content_sha256']!=packet.source.content_sha256 or manifest['source_document_id']!=packet.source.source_document_id or not 1<=manifest['page_count']<=100:
            raise ReviewError('ORIGINAL_SOURCE_MANIFEST_REQUIRED',409)
        return snap,packet,manifest

    @staticmethod
    def _signature(snap,manifest):
        return digest({'packet':snap['packet'],'dependencies':snap.get('dependency_digest'),
            'source_manifest':{k:manifest[k] for k in ('source_document_id','content_sha256','page_count')}})

    def _load(self,journal):
        state,_,_=journal.load()
        return state or {'schema_version':'fr7-source-check-v1','checks':{}}

    def _check(self,state,check_id,principal):
        check=state['checks'].get(str(check_id))
        if not check:raise ReviewError('SCHEDULE_CHECK_NOT_FOUND',404)
        snap,packet,manifest=self._current(check['task_id'],principal)
        if self._signature(snap,manifest)!=check['source_revision_digest']:
            self.invalidate(check['check_id'])
            raise ReviewError('SCHEDULE_CHECK_STALE_SOURCE_OR_REVISION',409)
        return check,snap,packet,manifest

    def invalidate(self,check_id):
        saved=self.tokens.pop(check_id,None)
        if saved:self.authority.revoke(saved[0])

    def _live_token(self,check_id):
        saved=self.tokens.get(check_id)
        if not saved:return None
        entry=self.verifiers.get(saved[1])
        try:
            if entry is None:raise ValueError('Verifier unavailable')
            self.store.authority.require(entry[1],'read')
            self.authority._require(entry[0])
        except (ValueError,ReviewError):
            self.authority.revoke(saved[0]);self.tokens.pop(check_id,None);return None
        return saved

    def _view(self,check,principal):
        result=dict(check)
        result['independent_access']=principal.grant_id in self.verifiers and principal.actor_id!=check['primary_actor']
        live=self._live_token(check['check_id'])
        result['current_complete_verification']=bool(live)
        if check['status']=='INDEPENDENTLY_CHECKED':
            result['status']='INDEPENDENTLY_CHECKED_REAUTH_REQUIRED' if not live else check['status']
        result['calendar_authorized']=False
        return result

    def start(self,body,principal):
        self.store.authority.require(principal,'correct')
        snap,packet,manifest=self._current(body.task_id,principal)
        if packet.source.timetable_id and body.scope.timetable_id!=packet.source.timetable_id:raise ReviewError('SCHEDULE_SCOPE_SOURCE_MISMATCH',422)
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            state=self._load(journal)
            if len(state['checks'])>=100:raise ReviewError('SCHEDULE_CHECK_CAPACITY',413)
            check={'check_id':str(uuid4()),'version':1,'task_id':body.task_id,
                'source_revision_digest':self._signature(snap,manifest),'source':packet.source.model_dump(mode='json'),
                'scope':body.scope.model_dump(mode='json'),'manifest':manifest,'status':'DRAFT',
                'synthetic':principal.mode=='TEST_ONLY','primary_actor':principal.actor_id,'primary':None,'verification':None,'blockers':[],
                'events':[e.model_dump(mode='json') for e in packet.events],
                'audit':[{'action':'START','actor_id':principal.actor_id,'recorded_at':utc_now().isoformat()}]}
            state['checks'][check['check_id']]=check;journal.append(state)
            return self._view(check,principal)

    def get(self,check_id,principal):
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            check,_,_,_=self._check(self._load(journal),check_id,principal)
            return self._view(check,principal)

    def latest(self,task_id,principal):
        snap,packet,manifest=self._current(task_id,principal)
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            checks=[c for c in self._load(journal)['checks'].values() if c['task_id']==task_id]
            if not checks:raise ReviewError('SCHEDULE_CHECK_NOT_FOUND',404)
            check=max(checks,key=lambda c:c['audit'][0]['recorded_at'])
            self._check({'checks':{check['check_id']:check}},check['check_id'],principal)
            return self._view(check,principal)

    def available_checks(self,task_id,principal):
        """Read-only discovery: a task ordering change must not hide a draft."""
        self.store.authority.require(principal,'correct')
        snap,packet,manifest=self._current(task_id,principal)
        signature=self._signature(snap,manifest);journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            state=self._load(journal);options=[];stale=0
            for check_id,check in state['checks'].items():
                if check['primary_actor']!=principal.actor_id or check['source']['source_document_id']!=packet.source.source_document_id or check['source']['content_sha256']!=packet.source.content_sha256:continue
                all_drafts=[d for d in state.get('drafts',{}).get(check_id,{}).values() if d['actor_id']==principal.actor_id]
                if check['source_revision_digest']!=signature:
                    stale+=bool(all_drafts);continue
                drafts=[d for d in all_drafts if d['check_version']==check['version'] and d['source_revision_digest']==signature]
                best=max(drafts,key=lambda d:d['updated_at']) if drafts else None
                options.append({'check_id':check_id,'version':check['version'],'scope':check['scope'],'saved_inventory':bool(check.get('primary')),
                    'recovery_drafts':len(drafts),'recovery_rows':len(best['form']['rows']) if best else 0,
                    'last_activity':best['updated_at'] if best else check['audit'][-1]['recorded_at']})
            return {'checks':sorted(options,key=lambda c:c['last_activity'],reverse=True),'stale_recovery_checks':stale}

    def save_draft(self,check_id,body,principal):
        self.store.authority.require(principal,'correct')
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            state=self._load(journal);check,_,_,_=self._check(state,check_id,principal)
            if check['primary_actor']!=principal.actor_id:raise ReviewError('PRIMARY_CHECK_OWNER_REQUIRED',403)
            if check['version']!=body.expected_version:raise ReviewError('STALE_SCHEDULE_CHECK',409)
            drafts=state.setdefault('drafts',{}).setdefault(check_id,{})
            key=str(body.client_id)
            if key not in drafts and len(drafts)>=8:raise ReviewError('SOURCE_DRAFT_CAPACITY',413)
            value=body.form.model_dump(mode='json');old=drafts.get(key)
            if old and old['check_version']==check['version'] and old['form']==value:return {'draft':old}
            record={'client_id':key,'actor_id':principal.actor_id,'check_version':check['version'],
                'source_revision_digest':check['source_revision_digest'],'form':value,
                'updated_at':utc_now().isoformat(),'purpose':'UNVALIDATED_RECOVERY_DRAFT_NOT_A_SOURCE_CHECK'}
            drafts[key]=record;journal.append(state)
            return {'draft':record}

    def recovery_drafts(self,check_id,principal):
        self.store.authority.require(principal,'correct')
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            state=self._load(journal);check,_,_,_=self._check(state,check_id,principal)
            if check['primary_actor']!=principal.actor_id:raise ReviewError('PRIMARY_CHECK_OWNER_REQUIRED',403)
            drafts=[d for d in state.get('drafts',{}).get(check_id,{}).values() if d['actor_id']==principal.actor_id and d['check_version']==check['version'] and d['source_revision_digest']==check['source_revision_digest']]
            return {'drafts':sorted(drafts,key=lambda d:d['updated_at'],reverse=True),'purpose':'UNVALIDATED_RECOVERY_ONLY'}

    def submit(self,check_id,body,principal):
        self.store.authority.require(principal,'correct')
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            state=self._load(journal);check,snap,packet,manifest=self._check(state,check_id,principal)
            if check['primary_actor']!=principal.actor_id:raise ReviewError('PRIMARY_CHECK_OWNER_REQUIRED',403)
            if check['version']!=body.expected_version:raise ReviewError('STALE_SCHEDULE_CHECK',409)
            known={e.candidate_id for e in packet.events};spans={s.evidence_id:s for s in packet.evidence};included=[];blockers=[]
            errors=[]
            def invalid(code,index,field,message):errors.append({'code':code,'path':['rows',index,field],'message':message})
            for index,row in enumerate(body.rows):
                if row.page>manifest['page_count']:invalid('SOURCE_INVENTORY_EVIDENCE_MISMATCH',index,'page','Choose an original page that exists in this source.')
                if any(e not in spans or (spans[e].page or 1)!=row.page for e in row.evidence_ids):invalid('SOURCE_INVENTORY_EVIDENCE_MISMATCH',index,'evidence_ids','Select source passages on the original page entered for this class.')
                if row.candidate_id and row.candidate_id not in known:invalid('SOURCE_INVENTORY_EVENT_MISMATCH',index,'candidate_id','This class does not belong to the current source check.')
                if row.decision=='INCLUDE':
                    if not row.candidate_id:invalid('INCLUDED_SESSION_EVIDENCE_REQUIRED',index,'candidate_id','Link this included session to a reconstructed class.')
                    if not row.evidence_ids:invalid('INCLUDED_SESSION_EVIDENCE_REQUIRED',index,'evidence_ids','Select the original class passage you inspected. A location or note alone does not select evidence.')
                    if row.candidate_id in known:
                        event=next(e for e in packet.events if e.candidate_id==row.candidate_id)
                        owned={ref for o in event.revision.observations for ref in o.evidence_ids}
                        if not set(row.evidence_ids)<=owned:invalid('SOURCE_SESSION_EVIDENCE_OWNERSHIP_REQUIRED',index,'evidence_ids','Choose evidence belonging to this class, rather than a different class.')
                    included.append(row.candidate_id)
                elif row.decision!='NON_SESSION':blockers.append('MISSING_OR_UNRESOLVED_SOURCE_SESSION')
                elif not row.note.strip():blockers.append('NON_SESSION_JUSTIFICATION_REQUIRED')
            if errors:
                error=ReviewError(errors[0]['code'],422);error.field_errors=errors;raise error
            if set(body.checked_pages)!=set(range(1,manifest['page_count']+1)):blockers.append('ALL_SOURCE_PAGES_MUST_BE_CHECKED')
            if len(included)!=len(set(included)) or set(included)!=known:blockers.append('SOURCE_SESSION_BIJECTION_REQUIRED')
            if len({row.source_session for row in body.rows})!=len(body.rows):blockers.append('DUPLICATE_SOURCE_SESSION_LABEL')
            if not body.source_confirmed or not body.scope_confirmed:blockers.append('PRIMARY_SOURCE_SCOPE_CONFIRMATION_REQUIRED')
            if any(t['state'] in {'REVALIDATION_REQUIRED','SUBMITTED_CORRECTION','ESCALATED'} for t in snap['tasks']):blockers.append('CURRENT_EVENT_REVALIDATION_REQUIRED')
            if packet.issues:blockers.append('SOURCE_PROCESSING_ISSUES_REQUIRE_ADJUDICATION')
            from backend.analysis.temporal_scope import project
            analysis_packet,_=project(check,packet)
            if any(o.state in {'UNRESOLVED','CONFLICTING'} for e in analysis_packet.events for o in e.revision.observations):blockers.append('UNRESOLVED_EVENT_FIELDS')
            if any(not e.revision.core_fields_present or e.extraction_status in {'AMBIGUOUS','CONFLICTING','UNAVAILABLE'} for e in analysis_packet.events):blockers.append('UNRESOLVED_EVENT_FIELDS')
            check.update(primary=body.model_dump(mode='json'),verification=None,blockers=sorted(set(blockers)),
                status='INCOMPLETE' if blockers else 'AWAITING_INDEPENDENT_REVIEW',version=check['version']+1)
            self.invalidate(check_id)
            check['audit'].append({'action':'PRIMARY_SOURCE_INVENTORY','actor_id':principal.actor_id,'recorded_at':utc_now().isoformat()})
            journal.append(state);return self._view(check,principal)

    def save_temporal(self,check_id,body,principal):
        from backend.analysis.temporal_scope import validate,project
        self.store.authority.require(principal,'correct')
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            state=self._load(journal);check,snap,packet,manifest=self._check(state,check_id,principal)
            if check['primary_actor']!=principal.actor_id:raise ReviewError('PRIMARY_CHECK_OWNER_REQUIRED',403)
            if check['version']!=body.expected_version:raise ReviewError('STALE_SCHEDULE_CHECK',409)
            if body.scope.timetable_id!=check['scope']['timetable_id']:raise ReviewError('SCHEDULE_SCOPE_SOURCE_MISMATCH',422)
            validate(body,packet,manifest)
            record={'record_id':'fr7-context:'+str(uuid4()),'actor_id':principal.actor_id,'recorded_at':utc_now().isoformat(),'submission':body.model_dump(mode='json')}
            check.update(temporal=record,scope=body.scope.model_dump(mode='json'),verification=None,status='DRAFT',version=check['version']+1)
            projected,_=project(check,packet)
            # Validate the projection and temporal constraints; errors stay visible,
            # not accepted merely because context was supplied.
            CanonicalTimetable.model_validate(projected.model_dump(mode='json'))
            check['context_events']=[e.model_dump(mode='json') for e in projected.events]
            assessment=student_analyze(self._request(check,snap,packet))
            check['context_diagnostics']=assessment['completeness']['reason_codes']
            check['blockers']=['PRIMARY_SOURCE_SCOPE_RECHECK_REQUIRED']
            if check.get('primary'):
                check['primary']['source_confirmed']=False;check['primary']['scope_confirmed']=False
            prior=self.tokens.pop(check_id,None)
            if prior:self.authority.revoke(prior[0])
            check['audit'].append({'action':'SOURCE_TEMPORAL_CONTEXT','actor_id':principal.actor_id,'recorded_at':record['recorded_at'],'context_digest':digest(record)})
            journal.append(state);return self._view(check,principal)

    def invitation_binding(self,check_id,principal):
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            check,snap,packet,manifest=self._check(self._load(journal),check_id,principal)
            authors={check['primary_actor']}
            for task in snap['tasks']:authors.update(c['actor_id'] for c in self.store.history(task['task_id'],principal)['corrections'])
            return digest({'check_id':check_id,'version':check['version'],'source_revision_digest':check['source_revision_digest'],'scope':check['scope'],'primary':check.get('primary'),'temporal':check.get('temporal')}),authors

    def reviewer_packet(self,check_id,principal):
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            check,snap,packet,manifest=self._check(self._load(journal),check_id,principal)
            return {'check':self._view(check,principal),'packet':packet.model_dump(mode='json')}

    def _request(self,check,snap,packet,verification=None,availability=()):
        from backend.analysis.temporal_scope import project
        scope=Scope.model_validate(check['scope']);packet,context=project(check,packet)
        if verification:
            timezones={e.revision.content.source_timezone for e in packet.events}
            if None in timezones:raise ReviewError('SOURCE_TIMEZONE_EVIDENCE_REQUIRED',409)
            raw=' '.join(s.raw_text for s in packet.evidence)
            if any(zone not in raw for zone in timezones):raise ReviewError('SOURCE_TIMEZONE_EVIDENCE_REQUIRED',409)
            eid='schedule-authority:'+verification['record_id']
            span=EvidenceSpan(evidence_id=eid,source_document_id=packet.source.source_document_id,route='authority',
                raw_text='Independently source-checked timezone: '+' '.join(sorted(timezones)),
                transformations=('fr7-independent-source-context-not-gold',))
            packet=packet.model_copy(update={'evidence':packet.evidence+(span,)})
            context=context.model_copy(update={'timezone_evidence_ids':context.timezone_evidence_ids or (eid,)})
        # Operational analysis scope identity, not an invented institutional ID.
        if packet.source.timetable_id is None:packet=packet.model_copy(update={'source':packet.source.model_copy(update={'timetable_id':scope.timetable_id})})
        return AnalysisInput(event_set_id='schedule-check:'+check['check_id'],profile='m7-student-v1',packet=packet,scope=scope,
            context=context,declared_kind='SYNTHETIC_DEMO' if check.get('synthetic') else 'HUMAN_REVIEWED_APPLICATION',availability=availability)

    def _inventory(self,check,request,verification):
        if not request.packet.events or not request.scope.cohort_id or any(e.revision.content.cohort_id!=request.scope.cohort_id for e in request.packet.events):raise ReviewError('WHOLE_SOURCE_CLASS_GROUP_UNRESOLVED',409)
        rows={r['candidate_id']:r for r in check['primary']['rows'] if r['decision']=='INCLUDE'}
        sessions=[]
        for event in request.packet.events:
            row=rows[event.candidate_id];occurrences,excluded=normalize_occurrences(request,event)
            sessions.append({'candidate_id':event.candidate_id,'revision_id':str(event.revision.revision_id),
                'source_session_id':row['source_session'],'page':row['page'],'region_id':row['source_session'],
                'source_reconciliation_record_id':verification['record_id'],
                'occurrence_digest':digest({'occurrences':[o.model_dump(mode='json') for o in occurrences],'excluded_dates':sorted(d.isoformat() for d in excluded)})})
        regions=[r['source_session'] for r in check['primary']['rows']]
        return {'source_document_id':request.packet.source.source_document_id,'source_sha256':request.packet.source.content_sha256,
            'required_pages':list(range(1,check['manifest']['page_count']+1)),'checked_pages':check['primary']['checked_pages'],
            'required_regions':regions,'checked_regions':regions,'sessions':sessions,'unresolved':[],'excluded_scope':[],
            'provenance_record_id':verification['record_id'],'recurrence_adjudication_record_id':verification['record_id'],
            'field_reconciliation_record_id':verification['record_id']}

    def _independent(self,check,snap,principal):
        entry=self.verifiers.get(principal.grant_id)
        verifier=entry[0] if entry else None
        authors={check['primary_actor']}
        for task in snap['tasks']:
            history=self.store.history(task['task_id'],principal)
            authors.update(r['actor_id'] for r in history['corrections'])
        if verifier is None or principal.actor_id in authors or not authors<=entry[2]:raise ReviewError('INDEPENDENT_VERIFIER_REQUIRED',403)
        self.authority._require(verifier)
        return verifier

    def verify(self,check_id,body,principal):
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            state=self._load(journal);check,snap,packet,_=self._check(state,check_id,principal)
            verifier=self._independent(check,snap,principal)
            if check['version']!=body.expected_version:raise ReviewError('STALE_SCHEDULE_CHECK',409)
            if check['status'] not in {'AWAITING_INDEPENDENT_REVIEW','INDEPENDENTLY_CHECKED'} or check['blockers']:raise ReviewError('PRIMARY_INVENTORY_INCOMPLETE',409)
            if not all((body.source_confirmed,body.inventory_confirmed,body.temporal_context_confirmed)):raise ReviewError('INDEPENDENT_SOURCE_CONFIRMATION_REQUIRED',422)
            verification={'actor_id':principal.actor_id,'record_id':'fr7:'+str(uuid4()),'recorded_at':utc_now().isoformat(),'note':body.note}
            check['synthetic']=principal.mode=='TEST_ONLY'
            request=self._request(check,snap,packet,verification)
            try:token=self.authority.record(request,self._inventory(check,request,verification),verifier)
            except ValueError:raise ReviewError('RECURRENCE_OR_INVENTORY_UNRESOLVED',409) from None
            evaluated=student_analyze(request.model_copy(update={'attestation_id':token}),self.authority)
            if not evaluated['completeness']['trusted_complete']:
                self.authority.revoke(token);raise ReviewError('NORMALIZATION_OR_SCOPE_UNRESOLVED',409)
            check.update(verification=verification,status='INDEPENDENTLY_CHECKED',version=check['version']+1)
            check['audit'].append({'action':'INDEPENDENT_SOURCE_VERIFICATION',**verification})
            journal.append(state);self.tokens[check_id]=(token,principal.grant_id)
            return self._view(check,principal)

    def source_page(self,check_id,page,raw,principal):
        from hashlib import sha256
        import base64,subprocess,sys
        from pathlib import Path
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            check,snap,packet,manifest=self._check(self._load(journal),check_id,principal)
            if packet.source.media_type!='application/pdf' or not 1<=page<=manifest['page_count'] or sha256(raw).hexdigest()!=packet.source.content_sha256:
                raise ReviewError('SOURCE_PREVIEW_HASH_OR_PAGE_MISMATCH',422)
            try:
                result=subprocess.run([sys.executable,'-B','-m','backend.analysis.source_preview',str(page)],input=raw,capture_output=True,timeout=15,cwd=Path(__file__).resolve().parents[2])
            except (OSError,subprocess.TimeoutExpired):raise ReviewError('SOURCE_PAGE_PREVIEW_UNAVAILABLE',503) from None
            if result.returncode or not result.stdout.startswith(b'\x89PNG') or len(result.stdout)>8*1024*1024:
                raise ReviewError('SOURCE_PAGE_PREVIEW_UNAVAILABLE',422)
            return {'page':page,'page_count':manifest['page_count'],'content_sha256':packet.source.content_sha256,'png_base64':base64.b64encode(result.stdout).decode('ascii')}

    def prepare_analysis(self,check_id,body,principal):
        from zoneinfo import ZoneInfo
        journal=self._transaction()
        with self.lock,self.store._lock,journal.locked():
            check,snap,packet,_=self._check(self._load(journal),check_id,principal)
            zone=ZoneInfo(check['scope']['timezone']);availability=[]
            for value in body.availability:
                left=datetime.fromisoformat(value.start);right=datetime.fromisoformat(value.end)
                availability.append(Availability(start=_local(left.date(),left.strftime('%H:%M'),zone),end=_local(right.date(),right.strftime('%H:%M'),zone)))
            verification=check['verification'];request=self._request(check,snap,packet,verification,tuple(availability))
            saved=self._live_token(check_id)
            if saved:request=request.model_copy(update={'attestation_id':saved[0]})
            return request,{'status':check['status'],'scope_bound':True,'independent_reauthorization_required':bool(verification and not saved)}

    def analyze(self,check_id,body,principal):
        request,meta=self.prepare_analysis(check_id,body,principal)
        result=student_analyze(request,self.authority)
        result['whole_schedule_check']=meta
        return result
