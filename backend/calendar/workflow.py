"""Protected TEST_ONLY application adapter over M6. Never creates a host or grant."""
from types import MappingProxyType
from backend.calendar.models import CalendarInput,CalendarError,Preview,Approval
from backend.calendar.sandbox import LocalSandbox
from backend.reconstruction.models import digest


class TestOnlyCalendarBridge:
    def __init__(self,sandbox,fixtures,*,allowed_origins=('http://127.0.0.1:5500','http://localhost:5500'),allow_failure_simulation=False):
        if type(sandbox) is not LocalSandbox or sandbox.snapshot_scope!='TEST_ONLY' or sandbox.fixtures is None:
            raise ValueError('An explicitly bootstrapped local TEST_ONLY sandbox is required')
        if not 1<=len(fixtures)<=16:raise ValueError('A bounded synthetic fixture catalogue is required')
        catalogue={}
        for key,(label,bundle) in fixtures.items():
            detached=CalendarInput.model_validate_json(bundle.model_dump_json())
            if not key or len(key)>80 or not sandbox.fixtures.authorizes(detached) or not detached.packet.source.source_document_id.startswith('synthetic:'):
                raise ValueError('Only host-registered invented fixtures are supported')
            if not detached.packet.events or any(e.revision.method_id!='synthetic-m6-fixture-v1' for e in detached.packet.events):
                raise ValueError('Synthetic fixture provenance is required')
            catalogue[key]=(str(label)[:200],detached.model_dump_json())
        self.sandbox=sandbox;self.catalogue=MappingProxyType(catalogue)
        self.allowed_origins=tuple(allowed_origins);self.allow_failure_simulation=bool(allow_failure_simulation)

    def require(self,actor,permission='read'):
        store=self.sandbox;store.access.require(actor,store.sandbox_id,permission)
        if actor.mode!='TEST_ONLY' or actor.permissions!=frozenset({'read'}) or not actor.source_bindings:
            raise CalendarError('TEST_ONLY_CALENDAR_GRANT_REQUIRED',403)
        # The entire dedicated target is in scope, including its history. Partial
        # source access cannot be widened by opening a shared target snapshot.
        for _,raw in self.catalogue.values():
            source=CalendarInput.model_validate_json(raw).packet.source
            if (source.source_document_id,source.content_sha256) not in actor.source_bindings:
                raise CalendarError('CALENDAR_SOURCE_SCOPE_DENIED',403)
        snapshot=store.snapshot(actor)
        if any((e.payload.source_document_id,e.payload.source_content_sha256) not in actor.source_bindings for e in snapshot.entries):
            raise CalendarError('CALENDAR_SOURCE_SCOPE_DENIED',403)
        known={digest(CalendarInput.model_validate_json(raw).model_dump(mode='json')) for _,raw in self.catalogue.values()}
        with store.journal.locked():state,_=store.journal.load()
        if any(p['input_digest'] not in known for p in state['previews'].values()):
            raise CalendarError('CALENDAR_TARGET_HISTORY_SCOPE_DENIED',403)

    def bundle(self,fixture_id,actor,permission='preview'):
        self.require(actor,permission)
        if fixture_id not in self.catalogue:raise CalendarError('SYNTHETIC_FIXTURE_NOT_FOUND',404)
        bundle=CalendarInput.model_validate_json(self.catalogue[fixture_id][1])
        if not self.sandbox.fixtures.authorizes(bundle):raise CalendarError('SYNTHETIC_REGISTRATION_REQUIRED',403)
        return bundle

    def session(self,actor):
        self.require(actor);store=self.sandbox
        permissions=sorted(store.access.permissions_for(actor,store.sandbox_id))
        return {'mode':'TEST_ONLY','actor_reference':actor.actor_id,'expires_at':actor.expires_at.isoformat(),
            'target_sandbox_id':store.sandbox_id,'permissions':permissions,'external_calendar':False,
            'failure_simulation_enabled':self.allow_failure_simulation,'storage':'M6_PERSISTENT_LOCAL_HASH_LINKED_JOURNAL',
            'fixtures':[{'fixture_id':key,'label':label,'source':CalendarInput.model_validate_json(raw).packet.source.model_dump(mode='json'),
                'events':[e.model_dump(mode='json') for e in CalendarInput.model_validate_json(raw).packet.events]} for key,(label,raw) in self.catalogue.items()]}

    def preview(self,fixture_id,actor):
        return self.sandbox.preview(self.bundle(fixture_id,actor),actor)

    def _plan(self,preview_digest,actor):
        self.require(actor)
        with self.sandbox.journal.locked():
            state,_=self.sandbox.journal.load();raw=state['previews'].get(preview_digest)
        if raw is None:raise CalendarError('PREVIEW_NOT_FOUND',404)
        plan=Preview.model_validate(raw)
        if plan.checksum()!=preview_digest:raise CalendarError('PREVIEW_DIGEST_MISMATCH',409)
        if plan.input_digest not in {digest(CalendarInput.model_validate_json(raw).model_dump(mode='json')) for _,raw in self.catalogue.values()}:
            raise CalendarError('CALENDAR_SOURCE_SCOPE_DENIED',403)
        return plan

    def approve(self,request,actor):
        self.require(actor,'approve');plan=self._plan(request.preview_digest,actor)
        keys=tuple(o.operation_key for o in plan.operations if o.action in {'CREATE','UPDATE','NO_OP'})
        if (request.target_sandbox_id!=plan.target_sandbox_id or request.snapshot_digest!=plan.snapshot_digest
                or request.input_digest!=plan.input_digest or request.operation_keys!=keys):
            raise CalendarError('EXACT_OWNER_APPROVAL_MISMATCH',409)
        return self.sandbox.approve(plan.preview_digest,actor)

    def approval_plan(self,approval_id,actor,permission):
        self.require(actor,permission)
        with self.sandbox.journal.locked():
            state,_=self.sandbox.journal.load();raw=state['approvals'].get(str(approval_id))
        if raw is None:raise CalendarError('APPROVAL_NOT_FOUND',404)
        approval=Approval.model_validate(raw)
        if approval.actor_id!=actor.actor_id:raise CalendarError('APPROVAL_ACTOR_OR_TARGET_MISMATCH',403)
        return self._plan(approval.preview_digest,actor)

    def execute(self,approval_id,actor,scenario='NONE',replay_only=False):
        plan=self.approval_plan(approval_id,actor,'execute');failures={}
        with self.sandbox.journal.locked():state,_=self.sandbox.journal.load()
        if state.get('approval_grants',{}).get(str(approval_id))!=actor.grant_id:
            raise CalendarError('APPROVAL_GRANT_MISMATCH',403)
        if replay_only:
            if not any(o['approval_id']==str(approval_id) for o in state['outcomes'].values()):
                raise CalendarError('NO_RECORDED_ATTEMPT',409)
        operations=[o.operation_key for o in plan.operations if o.action in {'CREATE','UPDATE','NO_OP'}]
        if scenario!='NONE':
            if not self.allow_failure_simulation:raise CalendarError('FAILURE_SIMULATION_DISABLED',403)
            if not operations:raise CalendarError('NO_APPROVABLE_ACTIONS',409)
            failures[operations[-1] if scenario=='PARTIAL_FAILURE' else operations[0]]='FAIL_BEFORE' if scenario=='PARTIAL_FAILURE' else scenario
            if scenario=='PARTIAL_FAILURE' and len(operations)<2:raise CalendarError('TWO_OPERATIONS_REQUIRED',422)
        return self.sandbox.execute(approval_id,actor,failures=failures)

    def reconcile(self,approval_id,actor):
        self.approval_plan(approval_id,actor,'reconcile')
        return self.sandbox.reconcile(approval_id,actor)

    def revoke(self,approval_id,actor):
        self.approval_plan(approval_id,actor,'approve')
        return self.sandbox.revoke_approval(approval_id,actor)

    def history(self,actor):
        self.require(actor);store=self.sandbox;diagnostics=store.diagnostics(actor)
        with store.journal.locked():state,records=store.journal.load()
        diagnostics['approvals']=[{**a,'revoked':key in state.get('revoked_approvals',{})}
            for key,a in state['approvals'].items() if a['actor_id']==actor.actor_id]
        diagnostics['approved_previews']=[state['previews'][key] for key in sorted({a['preview_digest'] for a in diagnostics['approvals']})]
        for record,view in zip(records,diagnostics['journal']):view['actor_reference']=record['actor_id']
        return diagnostics
