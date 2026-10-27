"""Persistent local-only approval/execution/reconciliation with no provider abstraction."""
from datetime import timedelta
import json
from uuid import uuid4

from backend.calendar.models import (CalendarError,Snapshot,Entry,Preview,Approval,Outcome,SourceBinding)
from backend.calendar.persistence import Journal
from backend.calendar.planning import build_preview,binding
from backend.reconstruction.models import digest
from backend.review.auth import utc_now

UNRESOLVED={'PENDING','OUTCOME_UNKNOWN','RECONCILIATION_REQUIRED'}


class LocalSandbox:
    def __init__(self,directory,sandbox_id,access,*,test_only=False,clock=utc_now):
        if not sandbox_id.startswith(('local:','test:')): raise CalendarError('LOCAL_SANDBOX_ID_REQUIRED',422)
        self.sandbox_id=sandbox_id;self.access=access;self.clock=clock;self.fixtures=None
        self.snapshot_scope='TEST_ONLY' if test_only else 'DEVELOPMENT_BLOCKED'
        self.journal=Journal(directory,test_only=test_only)
        with self.journal.locked():
            state,_=self.journal.load()
            scope='TEST_ONLY' if test_only else 'DEVELOPMENT_BLOCKED'
            if state is None:
                self.journal.append({'sandbox_id':sandbox_id,'scope':scope,'calendar_version':0,'entries':{},'sources':{},
                    'previews':{},'approvals':{},'outcomes':{},'observations':{}},'INITIALISE','trusted-host-bootstrap')
            elif state['sandbox_id']!=sandbox_id or state['scope']!=scope:
                raise CalendarError('SANDBOX_ID_OR_SCOPE_MISMATCH',409)

    def _snapshot(self,state):
        return Snapshot(sandbox_id=state['sandbox_id'],scope=state['scope'],calendar_version=state['calendar_version'],
                        entries=tuple(Entry.model_validate(v) for _,v in sorted(state['entries'].items())))

    def snapshot(self,principal):
        self.access.require(principal,self.sandbox_id,'read')
        with self.journal.locked(): return self._snapshot(self.journal.load()[0])

    def preview(self,bundle,principal):
        self.access.require(principal,self.sandbox_id,'preview')
        # Detach caller data and never accept a client-provided eligibility flag.
        bundle=type(bundle).model_validate_json(bundle.model_dump_json())
        with self.journal.locked():
            state,_=self.journal.load()
            test=state['scope']=='TEST_ONLY' and self.fixtures is not None and self.fixtures.authorizes(bundle)
            plan=build_preview(bundle,self._snapshot(state),test_authorized=test,clock=self.clock)
            family=digest({'family':bundle.packet.source.source_family_id,'timetable':bundle.packet.source.timetable_id,
                           'capture_fallback':bundle.packet.source.source_document_id if not bundle.packet.source.source_family_id else None})
            old_observation=state['observations'].get(family)
            epoch=(old_observation['version']+(old_observation['digest']!=plan.input_digest)) if old_observation else 1
            state['observations'][family]={'version':epoch,'digest':plan.input_digest}
            incoming={e.identity.stable_key for e in bundle.packet.events if e.identity.stable_key}
            for key,known in state['sources'].items():
                if known['family']==family and key not in incoming: known['observation_missing']=True
            for event in bundle.packet.events:
                if not event.identity.stable_key: continue
                if sum(e.identity.stable_key==event.identity.stable_key for e in bundle.packet.events)>1:
                    if event.identity.stable_key in state['sources']: state['sources'][event.identity.stable_key]['lineage_conflict']=True
                    continue
                ref=binding(bundle,event).model_copy(update={'observation_version':epoch});key=ref.stable_key;old=state['sources'].get(key)
                if old and old['binding']['revision_id']!=str(ref.revision_id):
                    if old['binding']['source_document_id']==ref.source_document_id:
                        if event.revision.version<=old['version'] or str(event.revision.previous_revision_id)!=old['binding']['revision_id']:
                            raise CalendarError('SOURCE_REVISION_CONFLICT',409)
                    elif not any(l.predecessor_document_id==old['binding']['source_document_id'] and l.successor_document_id==ref.source_document_id
                                 for l in bundle.context.lineage):
                        # Conflicting captures remain visible as a refused preview;
                        # they must not replace the authorized source registry.
                        old['lineage_conflict']=True
                        continue
                if old and old['binding']['revision_id']==str(ref.revision_id) and old['binding']['revision_digest']!=ref.revision_digest:
                    raise CalendarError('SOURCE_REVISION_DIGEST_CONFLICT',409)
                state['sources'][key]={'binding':ref.model_dump(mode='json'),'version':event.revision.version,'test_authorized':bool(test),
                                     'family':family,'observation_missing':False,'lineage_conflict':False}
            plan=plan.model_copy(update={'source_bindings':tuple(ref.model_copy(update={'observation_version':epoch}) for ref in plan.source_bindings)})
            plan=plan.model_copy(update={'preview_digest':plan.checksum()})
            state['previews'][plan.preview_digest]=plan.model_dump(mode='json')
            self.journal.append(state,'PREVIEW',principal.actor_id,{'preview_digest':plan.preview_digest,'input_digest':plan.input_digest})
            return plan

    def _current_sources(self,state,plan):
        for ref in plan.source_bindings:
            source=state['sources'].get(ref.stable_key)
            if source and (source['observation_missing'] or source['lineage_conflict']):
                raise CalendarError('SOURCE_OBSERVATION_REVIEW_REQUIRED',409)
            if not source or source['binding']!=ref.model_dump(mode='json'):
                raise CalendarError('STALE_SOURCE_REVISION',409)
            if not source['test_authorized']: raise CalendarError('OPERATIONAL_WRITE_BLOCKED',403)

    def approve(self,preview_digest,principal,*,decision='APPROVE'):
        self.access.require(principal,self.sandbox_id,'approve')
        with self.journal.locked():
            state,_=self.journal.load()
            if state['scope']!='TEST_ONLY': raise CalendarError('OPERATIONAL_WRITE_BLOCKED',403)
            raw=state['previews'].get(preview_digest)
            if raw is None: raise CalendarError('PREVIEW_NOT_FOUND',404)
            plan=Preview.model_validate(raw)
            if plan.checksum()!=preview_digest: raise CalendarError('PREVIEW_DIGEST_MISMATCH',409)
            self._current_sources(state,plan)
            snap=self._snapshot(state)
            if snap.calendar_version!=plan.snapshot_version or snap.checksum()!=plan.snapshot_digest:
                raise CalendarError('STALE_PREVIEW',409)
            actionable=tuple(o.operation_key for o in plan.operations if o.action in {'CREATE','UPDATE','NO_OP'})
            if not actionable: raise CalendarError('NO_APPROVABLE_ACTIONS',409)
            for old in state['outcomes'].values():
                if old['operation_key'] in actionable and old['status'] in UNRESOLVED:
                    raise CalendarError('RECONCILIATION_REQUIRED',409)
            if decision not in {'APPROVE','REJECT'}: raise CalendarError('INVALID_APPROVAL_DECISION',422)
            now=self.clock()
            approval=Approval(approval_id=uuid4(),actor_id=principal.actor_id,target_sandbox_id=self.sandbox_id,
                preview_digest=plan.preview_digest,snapshot_version=plan.snapshot_version,snapshot_digest=plan.snapshot_digest,
                source_bindings=plan.source_bindings,operation_keys=actionable,decision=decision,recorded_at=now,
                expires_at=now+timedelta(minutes=10),authority_kind='ISOLATED_SYNTHETIC_TEST')
            state['approvals'][str(approval.approval_id)]=approval.model_dump(mode='json')
            # Negative authority and grant binding are additive journal metadata;
            # historical records remain readable without rewriting them.
            state.setdefault('approval_grants',{})[str(approval.approval_id)]=principal.grant_id
            self.journal.append(state,'APPROVAL',principal.actor_id,{'approval_id':str(approval.approval_id),'decision':decision})
            return approval

    def _approval(self,state,approval_id,principal):
        raw=state['approvals'].get(str(approval_id))
        if raw is None: raise CalendarError('APPROVAL_REQUIRED',403)
        approved=Approval.model_validate(raw)
        if str(approval_id) in state.get('revoked_approvals',{}):
            raise CalendarError('APPROVAL_REVOKED',403)
        if state.get('approval_grants',{}).get(str(approval_id),principal.grant_id)!=principal.grant_id:
            raise CalendarError('APPROVAL_GRANT_MISMATCH',403)
        if approved.actor_id!=principal.actor_id or approved.target_sandbox_id!=self.sandbox_id:
            raise CalendarError('APPROVAL_ACTOR_OR_TARGET_MISMATCH',403)
        if approved.decision!='APPROVE': raise CalendarError('APPROVAL_REJECTED',403)
        if approved.expires_at<=self.clock(): raise CalendarError('APPROVAL_EXPIRED',409)
        plan=Preview.model_validate(state['previews'][approved.preview_digest])
        keys=tuple(o.operation_key for o in plan.operations if o.action in {'CREATE','UPDATE','NO_OP'})
        if plan.checksum()!=approved.preview_digest or keys!=approved.operation_keys or plan.source_bindings!=approved.source_bindings:
            raise CalendarError('APPROVAL_PAYLOAD_MISMATCH',409)
        self._current_sources(state,plan)
        return approved,plan

    def revoke_approval(self,approval_id,principal):
        self.access.require(principal,self.sandbox_id,'approve')
        with self.journal.locked():
            state,_=self.journal.load();raw=state['approvals'].get(str(approval_id))
            if raw is None:raise CalendarError('APPROVAL_NOT_FOUND',404)
            if raw['actor_id']!=principal.actor_id:raise CalendarError('APPROVAL_ACTOR_OR_TARGET_MISMATCH',403)
            revoked=state.setdefault('revoked_approvals',{})
            if str(approval_id) not in revoked:
                revoked[str(approval_id)]={'actor_id':principal.actor_id,'recorded_at':self.clock().isoformat()}
                self.journal.append(state,'REVOKE_APPROVAL',principal.actor_id,{'approval_id':str(approval_id)})
            return {'approval_id':str(approval_id),'revoked':True,'external_calendar':False}

    def execute(self,approval_id,principal,*,failures=None):
        self.access.require(principal,self.sandbox_id,'execute')
        failures=failures or {}
        allowed={'FAIL_BEFORE','TIMEOUT_BEFORE','TIMEOUT_AFTER','CRASH_AFTER_SUBMIT','TIMEOUT_AFTER_MISMATCH'}
        if any(mode not in allowed for mode in failures.values()): raise CalendarError('INVALID_FAILURE_SIMULATION',422)
        with self.journal.locked():
            state,_=self.journal.load()
            if state['scope']!='TEST_ONLY': raise CalendarError('OPERATIONAL_WRITE_BLOCKED',403)
            approved,plan=self._approval(state,approval_id,principal)
            prefix=str(approved.approval_id)+':'
            prior={key:state['outcomes'].get(prefix+key) for key in approved.operation_keys}
            if any(prior.values()):
                # Never resend any part of an issued set under the old approval.
                # An interrupted unissued tail also requires a fresh preview.
                result=[]
                for op in plan.operations:
                    if op.operation_key not in approved.operation_keys: continue
                    old=prior[op.operation_key]
                    if not old:
                        out=Outcome(operation_key=op.operation_key,approval_id=approved.approval_id,status='PREVIEW_REQUIRED',
                                    reason_code='INTERRUPTED_SET_REQUIRES_FRESH_APPROVAL',entry_id=op.entry_id)
                    else:
                        out=Outcome.model_validate(old).model_copy(update={'duplicate_submission':True})
                        current=state['entries'].get(op.entry_id)
                        if out.status=='CONFIRMED' and (not current or Entry.model_validate(current).payload!=op.after):
                            out=out.model_copy(update={'status':'RECONCILIATION_REQUIRED','reason_code':'CONFIRMED_STATE_CHANGED'})
                        elif out.status=='PENDING':
                            out=out.model_copy(update={'status':'OUTCOME_UNKNOWN','reason_code':'INTERRUPTED_OPERATION'})
                    result.append(out)
                return tuple(result)
            snap=self._snapshot(state)
            if snap.calendar_version!=approved.snapshot_version or snap.checksum()!=approved.snapshot_digest:
                raise CalendarError('STALE_APPROVAL',409)
            result=[]
            for op in plan.operations:
                if op.operation_key not in approved.operation_keys: continue
                key=prefix+op.operation_key
                pending=Outcome(operation_key=op.operation_key,approval_id=approved.approval_id,status='PENDING',reason_code='SUBMITTED_LOCAL_OPERATION',entry_id=op.entry_id)
                state['outcomes'][key]=pending.model_dump(mode='json')
                self.journal.append(state,'SUBMIT',principal.actor_id,{'operation_key':op.operation_key,'approval_id':str(approved.approval_id)})
                mode=failures.get(op.operation_key)
                if mode=='CRASH_AFTER_SUBMIT': raise RuntimeError('SYNTHETIC_INTERRUPTION_AFTER_DURABLE_SUBMISSION')
                if mode in {'FAIL_BEFORE','TIMEOUT_BEFORE'}:
                    outcome=pending.model_copy(update={'status':'FAILED_CONFIRMED' if mode=='FAIL_BEFORE' else 'OUTCOME_UNKNOWN',
                                                       'reason_code':'SIMULATED_FAILURE' if mode=='FAIL_BEFORE' else 'NETWORK_TIMEOUT_SIMULATION'})
                else:
                    current=state['entries'].get(op.entry_id)
                    matches=[e for e in state['entries'].values() if e['payload']['stable_key']==op.stable_key]
                    valid=(op.action=='CREATE' and not matches and current is None or op.action in {'UPDATE','NO_OP'} and
                           len(matches)==1 and current and Entry.model_validate(current).managed and Entry.model_validate(current).payload==op.before)
                    if not valid:
                        outcome=pending.model_copy(update={'status':'FAILED_CONFIRMED','reason_code':'CONCURRENT_TARGET_CONFLICT'})
                    else:
                        payload=op.after
                        if mode=='TIMEOUT_AFTER_MISMATCH': payload=payload.model_copy(update={'title':'SYNTHETIC MISMATCH'})
                        if op.action!='NO_OP':
                            state['entries'][op.entry_id]=Entry(entry_id=op.entry_id,payload=payload).model_dump(mode='json')
                            state['calendar_version']+=1
                        timeout=mode in {'TIMEOUT_AFTER','TIMEOUT_AFTER_MISMATCH'}
                        outcome=pending.model_copy(update={'status':'OUTCOME_UNKNOWN' if timeout else 'CONFIRMED',
                            'reason_code':'NETWORK_TIMEOUT_SIMULATION' if timeout else 'LOCAL_STATE_OBSERVED',
                            'observed_digest':None if timeout else digest(state['entries'][op.entry_id])})
                state['outcomes'][key]=outcome.model_dump(mode='json')
                self.journal.append(state,'LOCAL_OUTCOME',principal.actor_id,{'operation_key':op.operation_key,'status':outcome.status})
                result.append(outcome)
            return tuple(result)

    def reconcile(self,approval_id,principal):
        self.access.require(principal,self.sandbox_id,'reconcile')
        with self.journal.locked():
            state,_=self.journal.load()
            # Reconciliation remains permitted after approval expiry/source changes;
            # it observes prior intent and cannot issue a mutation.
            raw=state['approvals'].get(str(approval_id))
            if raw is None: raise CalendarError('APPROVAL_NOT_FOUND',404)
            approved=Approval.model_validate(raw)
            if approved.actor_id!=principal.actor_id: raise CalendarError('APPROVAL_ACTOR_OR_TARGET_MISMATCH',403)
            plan=Preview.model_validate(state['previews'][approved.preview_digest]);result=[]
            for op in plan.operations:
                key=str(approved.approval_id)+':'+op.operation_key
                if op.operation_key not in approved.operation_keys: continue
                previous=state['outcomes'].get(key)
                if not previous: continue
                matches=[e for e in state['entries'].values() if e['payload']['stable_key']==op.stable_key and e['managed']]
                if len(matches)==1 and Entry.model_validate(matches[0]).payload==op.after:
                    status,reason='CONFIRMED','LOCAL_STATE_OBSERVED'
                    observed=digest(matches[0])
                elif not matches and op.action=='CREATE' or len(matches)==1 and Entry.model_validate(matches[0]).payload==op.before:
                    status,reason='PREVIEW_REQUIRED','CONFIRMED_NON_APPLICATION';observed=None
                else: status,reason='RECONCILIATION_REQUIRED','OBSERVED_STATE_MISMATCH';observed=None
                out=Outcome.model_validate(previous).model_copy(update={'status':status,'reason_code':reason,'observed_digest':observed})
                state['outcomes'][key]=out.model_dump(mode='json');result.append(out)
            self.journal.append(state,'RECONCILE',principal.actor_id,{'approval_id':str(approval_id),'statuses':[r.status for r in result]})
            return tuple(result)

    def diagnostics(self,principal):
        self.access.require(principal,self.sandbox_id,'read')
        with self.journal.locked():
            state,history=self.journal.load()
            return {'sandbox_id':self.sandbox_id,'scope':state['scope'],'calendar_version':state['calendar_version'],
                'entry_count':len(state['entries']),'outcomes':list(state['outcomes'].values()),
                'journal':[{'sequence':r['sequence'],'operation':r['operation'],'record_hash':r['record_hash'],
                            'previous_hash':r['previous_hash'],'recorded_at':r['recorded_at'],'details':r['details']} for r in history],
                'staging_file_count':len(list(self.journal.directory.glob('*.staging'))),'external_calendar':False}
