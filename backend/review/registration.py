"""Host-owned PRE-01/02/03 authority. No HTTP registration or implicit approval.

File approval hashes are trusted bootstrap inputs, never read from the proposed
manifest itself. Synthetic registrations only cover exact invented captures.
"""
import hashlib,hmac,json,secrets
from functools import wraps
from threading import RLock
from datetime import datetime,timezone
from pathlib import Path
from pydantic import Field
from backend.models.evidence import ContractModel
from backend.models.event import DecisionStatus,FieldState,CanonicalTimetable
from backend.reconstruction.models import digest
from backend.review.selection import verify_validation,REVIEW_FLOOR
from backend.review.models import PolicyAssessment

class RuntimeFeatures(ContractModel):
    revision_digest: str=Field(pattern=r'^[0-9a-f]{64}$')
    x: float|None=Field(default=None,ge=0,le=1,allow_inf_nan=False)
    r: float|None=Field(default=None,ge=0,le=1,allow_inf_nan=False)
    c: float|None=Field(default=None,ge=0,le=1,allow_inf_nan=False)
    penalty: float|None=Field(default=None,ge=0,allow_inf_nan=False)


class FrozenM4Features:
    """Recompute native M4 indices using the exact frozen settings/selection.

    No calibration or probability is implied. Edited revisions cannot inherit
    original candidate scores: their provider must first be validated separately.
    """
    provider_id='m4-frozen-features-v1'

    def __init__(self,settings):
        from backend.reconstruction.models import Settings
        self._settings=Settings.model_validate_json(settings.model_dump_json())

    def validate_configuration(self,config):
        expected=tuple(self._settings.base_weights[k] for k in ('extraction','relations','completeness'))
        if config.settings_digest!=digest(self._settings.model_dump(mode='json')) or config.weights!=expected:
            raise ValueError('Frozen reconstruction and routing settings differ')

    def __call__(self,run,event,evidence,findings):
        from backend.review.binding import recover_selection
        from backend.reconstruction.scoring import score
        graph=run.graph();settings=self._settings
        if graph is None or graph.settings_digest!=digest(settings.model_dump(mode='json')) or event.revision.version!=1:
            return None
        group_id=dict(run.candidate_groups).get(event.candidate_id)
        group=next((g for g in graph.groups if g.group_id==group_id),None)
        if group is None:return None
        selection=recover_selection(group,event,graph,run.decoder_mode)
        if selection is None:return None
        record=score(group,selection,graph,settings)
        components=record['components']
        penalty=sum(record['soft_magnitudes'][k]*w/100000000 for k,w in settings.soft_weights.items())
        return RuntimeFeatures(revision_digest=digest(event.revision.model_dump(mode='json')),
            x=components['extraction']/10000,r=components['relations']/10000,
            c=components['completeness']/10000,penalty=penalty)


def locked(method):
    @wraps(method)
    def invoke(self,*args,**kwargs):
        with self._lock:return method(self,*args,**kwargs)
    return invoke

class OperatingRegistry:
    def __init__(self,*,trusted_approval_digests=(),feature_provider=None):
        self._trusted=frozenset(trusted_approval_digests);self._provider=feature_provider
        self._key=secrets.token_bytes(32);self._issued={};self._active=None;self._lock=RLock();self._runtime_files={};self._latest={};self._current_receipts={}
    @property
    def active(self):return self._active is not None
    @property
    def mode(self):return self._active['mode'] if self.active else 'NONE'
    def _install(self,mode,config,tau,pre02,pre03,bindings=()):
        if self._provider is None or self._provider.provider_id!=config.provider_id:raise ValueError('Frozen feature provider unavailable')
        if hasattr(self._provider,'validate_configuration'):self._provider.validate_configuration(config)
        self._active={'mode':mode,'config':config.model_dump_json(),'tau':tau,'bindings':frozenset(tuple(b) for b in bindings),
            'registration_id':digest({'mode':mode,'configuration':config.checksum(),'pre02':pre02['integrity_digest'],'pre03':pre03['integrity_digest'],'scope':sorted(bindings)})}
        self._issued.clear();self._key=secrets.token_bytes(32)
    @locked
    def register_test_only(self,development,pre02,validation,pre03,*,source_bindings):
        self.revoke()
        if development.origin!='SYNTHETIC' or validation.origin!='SYNTHETIC' or not source_bindings or any(not b[0].startswith('synthetic:') for b in source_bindings):raise ValueError('Synthetic registration requires exact invented capture scope')
        config,tau=verify_validation(pre03,validation,pre02,development)
        self._install('TEST_ONLY',config,tau,pre02,pre03,source_bindings)
    @locked
    def register(self,development,pre02,validation,pre03,*,bundle,evidence_root,approval_artifact_id,runtime_files,approval_artifact=None):
        self.revoke()
        from backend.research.readiness import assess_pre01
        from backend.research.integrity import safe_path,file_sha256,source_for_page
        if development.origin!='AUTHENTIC' or validation.origin!='INDEPENDENT_VALIDATION_CLAIM' or bundle.origin!='AUTHENTIC':raise ValueError('Synthetic/development evidence cannot register genuine ACCEPT')
        report=assess_pre01(bundle,Path(evidence_root))
        if report.status!='READY' or digest(report.model_dump(mode='json'))!=development.pre01_artifact_digest:raise ValueError('Authentic PRE-01 closure missing or changed')
        config,tau=verify_validation(pre03,validation,pre02,development)
        if max(bundle.gold_freeze.frozen_by.recorded_at,bundle.closure_attestation.recorded_at)>datetime.fromisoformat(pre02['recorded_at']):raise ValueError('PRE-01 must be closed before PRE-02')
        # Approval is a later artefact, outside the immutable PRE-01 bundle.
        # Including it in that bundle would create a circular freeze digest.
        approval=approval_artifact
        if approval is None or approval.artifact_id!=approval_artifact_id or approval.origin!='AUTHENTIC' or approval.kind!='operating_point_approval' or approval.sha256 not in self._trusted:raise ValueError('Explicit trusted operating-point approval required')
        if file_sha256(safe_path(Path(evidence_root),approval.relative_path))!=approval.sha256:raise ValueError('Approval file hash mismatch')
        verified_files=_verify_runtime_files(config,runtime_files,Path(evidence_root))
        # These inputs are part of the authority, not only startup diagnostics.
        for artifact in (*bundle.artifacts,*bundle.gold_freeze.artifacts,approval):
            verified_files[str(safe_path(Path(evidence_root),artifact.relative_path))]=artifact.sha256
        for source in bundle.sources:
            verified_files[str(safe_path(Path(evidence_root),source.source_path))]=source.original_sha256
        pages={p.page_id:p for p in bundle.pages}
        gold_hashes={a.sha256 for a in bundle.gold_freeze.artifacts if a.kind=='gold_manifest' and a.origin=='AUTHENTIC'}
        for partition,rows in [('development',tuple(r for c in development.candidates for r in c.rows)),('validation',validation.rows)]:
            expected_pages={p.page_id for p in bundle.pages if p.partition==partition and p.timetable_bearing}
            populations=[c.rows for c in development.candidates] if partition=='development' else [rows]
            if any({r.source_page_id for r in population}!=expected_pages for population in populations):raise ValueError('Whole registered partition required')
            for row in rows:
                page=pages.get(row.source_page_id)
                if page is None or page.partition!=partition or not page.timetable_bearing:raise ValueError('Reference partition or source page mismatch')
                source=source_for_page(bundle.sources,page)
                if source is None or source.original_sha256!=row.source_sha256 or row.reference_digest not in gold_hashes:raise ValueError('Frozen source/reference provenance mismatch')
        record=json.loads(safe_path(Path(evidence_root),approval.relative_path).read_text(encoding='utf-8'))
        expected={'origin':'AUTHENTIC','kind':'PRE03_OPERATING_POINT_APPROVAL','pre01_input_digest':report.input_digest,
            'pre02_digest':pre02['integrity_digest'],'pre03_digest':pre03['integrity_digest'],'configuration_digest':config.checksum(),'pipeline_digest':config.pipeline_digest()}
        if any(record.get(k)!=v for k,v in expected.items()) or not record.get('approved_by'):raise ValueError('Approval does not bind the exact operating point')
        stamp=datetime.fromisoformat(record['recorded_at'])
        if stamp.tzinfo is None or not datetime.fromisoformat(pre03['recorded_at'])<=stamp<=datetime.now(timezone.utc):raise ValueError('Approval chronology invalid')
        self._install('AUTHENTIC',config,tau,pre02,pre03)
        self._runtime_files=verified_files
    @locked
    def load(self,*,development_path,pre02_path,validation_path,pre03_path,**authority):
        self.revoke()
        from backend.review.selection import DevelopmentInput
        from backend.review.operating_point import ValidationInput
        paths=(development_path,pre02_path,validation_path,pre03_path)
        raw=[Path(path).read_bytes() for path in paths]
        dev=DevelopmentInput.model_validate_json(raw[0]);val=ValidationInput.model_validate_json(raw[2])
        self.register(dev,json.loads(raw[1]),val,json.loads(raw[3]),**authority)
        self._runtime_files.update({str(Path(p).resolve()):hashlib.sha256(b).hexdigest() for p,b in zip(paths,raw)})
    @locked
    def load_test_only(self,*,development_path,pre02_path,validation_path,pre03_path,source_bindings):
        """Explicit isolated-host loader. Never installed by student startup."""
        self.revoke()
        from backend.review.selection import DevelopmentInput
        from backend.review.operating_point import ValidationInput
        dev=DevelopmentInput.model_validate_json(Path(development_path).read_text(encoding='utf-8'))
        val=ValidationInput.model_validate_json(Path(validation_path).read_text(encoding='utf-8'))
        self.register_test_only(dev,json.loads(Path(pre02_path).read_text(encoding='utf-8')),val,json.loads(Path(pre03_path).read_text(encoding='utf-8')),source_bindings=source_bindings)
    @locked
    def revoke(self):
        self._active=None;self._issued.clear();self._key=secrets.token_bytes(32);self._runtime_files={};self._latest.clear();self._current_receipts.clear()
    def _current(self):
        from backend.research.integrity import file_sha256
        try:
            if any(file_sha256(Path(p))!=h for p,h in self._runtime_files.items()):self.revoke()
        except OSError:self.revoke()
        return self.active
    def _source_allowed(self,source):
        return self.mode=='AUTHENTIC' or self.mode=='TEST_ONLY' and (source.source_document_id,source.content_sha256) in self._active['bindings'] and source.source_document_id.startswith('synthetic:')
    @locked
    def route(self,run,event,findings,base,*,source_evidence=None):
        from backend.review.selection import FrozenConfiguration
        from fractions import Fraction
        if not self._current() or event is None:return base
        spec=FrozenConfiguration.model_validate_json(self._active['config'])
        if run.profile!=spec.profile:return base
        graph=run.graph();source=CanonicalTimetable.model_validate_json(run.source_json).source
        if not self._source_allowed(source):return base
        event_digest=digest(event.revision.model_dump(mode='json'))
        scope=(source.source_document_id,source.content_sha256,event.candidate_id)
        previous=self._latest.get(scope)
        revision=(event.revision.version,str(event.revision.revision_id),event_digest)
        if previous and previous!=revision:
            if revision[0]<=previous[0] or str(event.revision.previous_revision_id)!=previous[1]:return base
        self._latest[scope]=revision
        refs=tuple(source_evidence if source_evidence is not None else graph.evidence if graph else ())
        group_id=dict(run.candidate_groups).get(event.candidate_id)
        relevant=tuple(f for f in findings if not f.group_ids or group_id in f.group_ids)
        pending=any(c in base.reason_codes for c in ('REVALIDATION_REQUIRED','INVALID_CORRECTION'))
        source_reasons=set(base.source_routing.reason_codes) if base.source_routing else set()
        unsafe_source=bool(source_reasons & {'MISSING_CORE_EVIDENCE','CORE_READING_AMBIGUOUS','UNOWNED_FIELD_EVIDENCE',
            'RECURRENCE_EVIDENCE_INCOMPLETE','CONSTRAINT_FAILURE','CONSTRAINT_CHECK_INCOMPLETE',
            'DUPLICATE_REQUIRES_RESOLUTION','SOURCE_DAY_DATE_CONFLICT','INCOMPLETE_RECONSTRUCTION'})
        complete=run.search_status=='COMPLETE_FINITE_SEARCH' and dict(run.component_statuses).get(group_id)!='SEARCH_INCOMPLETE' and event.candidate_id not in run.unresolved_ids
        valid=bool(graph and graph.settings_digest==spec.settings_digest and run.decoder_mode=='constrained' and complete and event.revision.core_fields_present and {'S01','S02','T01','R01'}<={f.rule_id for f in relevant} and all(f.passed is True for f in relevant if f.severity=='HARD') and not pending and not unsafe_source)
        observations=event.revision.observations
        ambiguous=[o for o in observations if o.state in {FieldState.UNRESOLVED,FieldState.CONFLICTING} or len(o.alternatives)>1]
        by_field={o.field_name:o for o in observations}
        missing={name for name in spec.required_accept_fields if name not in by_field or by_field[name].state==FieldState.MISSING}
        known={s.evidence_id for s in refs if s.source_document_id==source.source_document_id}
        trace=bool(event.identity.evidence_ids) and set(event.identity.evidence_ids)<=known and all(set(o.evidence_ids)<=known for o in observations) and any(o.evidence_ids for o in observations)
        critical_ambiguity=bool(missing & {'recurrence','teaching_weeks','excluded_weeks'}) or any(o.field_name in {'recurrence','teaching_weeks','excluded_weeks'} for o in ambiguous)
        duplicate=any(f.rule_id=='DUPLICATE_DIAGNOSTIC' and f.passed is False for f in relevant)
        identity=event.identity.status=='CONFIRMED'
        q=valid and trace and len({o.field_name for o in ambiguous}|missing)<=2 and identity and not critical_ambiguity and not duplicate
        soft='STRUCTURE_CHECK_REQUIRED' in source_reasons or any(f.severity=='SOFT' and f.passed is not True for f in relevant)
        unresolved=bool(ambiguous) or any(name not in by_field or not (by_field[name].state==FieldState.PRESENT or by_field[name].state in {FieldState.SOURCE_ABSENT,FieldState.NOT_APPLICABLE} and by_field[name].evidence_ids and by_field[name].justification) for name in spec.required_accept_fields)
        try:
            features=self._provider(run,event,refs,relevant) if valid else None
            if features is not None:features=RuntimeFeatures.model_validate(features)
        except (ValueError,KeyError,TypeError):features=None
        score=None
        if features is not None:
            if features.revision_digest!=digest(event.revision.model_dump(mode='json')):valid=False
            elif all(v is not None or w==0 for v,w in zip((features.x,features.r,features.c),spec.weights)) and (features.penalty is not None or spec.penalty_weight==0):
                score=max(Fraction(0),min(Fraction(1),sum(Fraction(str(v or 0))*w/10000 for v,w in zip((features.x,features.r,features.c),spec.weights))-Fraction(str(spec.penalty_weight))*Fraction(str(features.penalty or 0))))
        route='ABSTAIN'
        if valid and score is not None:
            if identity and trace and not duplicate and not unresolved and not soft and score>=Fraction(str(self._active['tau'])):route='ACCEPT'
            elif q and score>=Fraction(str(REVIEW_FLOOR)):route='REVIEW'
        updates={'decision':DecisionStatus(route),'policy_profile':'m5-registered-v1','policy_version':'1.0','policy_digest':spec.checksum(),
            'registered_routing_eligible':route=='ACCEPT','registration_mode':self.mode,'registration_id':self._active['registration_id'],
            'registration_source_digest':digest(source.model_dump(mode='json')),'registration_evidence_digest':digest([s.model_dump(mode='json') for s in refs]),
            'registration_signature':None,'ranking_score':float(score) if score is not None else None,
            'score_provenance':'REGISTERED_FROZEN_RANKING','reason_codes':('REGISTERED_'+route,), 'review_required':route!='ACCEPT'}
        record=base.model_copy(update=updates)
        body=record.model_dump(mode='json');body.pop('registration_signature',None)
        signature=hmac.new(self._key,digest(body).encode(),hashlib.sha256).hexdigest()
        record=record.model_copy(update={'registration_signature':signature})
        self._issued[str(record.decision_id)]=record.model_dump_json()
        self._current_receipts[scope]=str(record.decision_id)
        return record
    @locked
    def resolve(self,ref):
        raw=self._issued.get(str(ref.decision_id))
        if not self._current() or raw is None:raise ValueError('Unknown or expired registered assessment')
        record=PolicyAssessment.model_validate_json(raw)
        for name in ('registration_id','registration_signature','candidate_id','event_id','revision_id','revision_digest'):
            if getattr(record,name)!=getattr(ref,name):raise ValueError('Registered assessment reference mismatch')
        if not any(candidate==record.candidate_id and state[1:]==(str(record.revision_id),record.revision_digest) for (_,_,candidate),state in self._latest.items()):raise ValueError('Registered assessment revision is stale')
        if str(record.decision_id) not in self._current_receipts.values():raise ValueError('Registered assessment was superseded')
        return record
    @locked
    def verifies(self,record,packet,event):
        if not self._current() or record.registration_mode!=self.mode or record.registration_id!=self._active['registration_id'] or not self._source_allowed(packet.source):return False
        if (record.candidate_id,record.event_id,record.revision_id,record.revision_digest)!=(event.candidate_id,event.identity.event_id,event.revision.revision_id,digest(event.revision.model_dump(mode='json'))):return False
        if self._latest.get((packet.source.source_document_id,packet.source.content_sha256,event.candidate_id))!=(event.revision.version,str(event.revision.revision_id),record.revision_digest):return False
        if self._current_receipts.get((packet.source.source_document_id,packet.source.content_sha256,event.candidate_id))!=str(record.decision_id):return False
        if record.registration_source_digest!=digest(packet.source.model_dump(mode='json')) or record.registration_evidence_digest!=digest([s.model_dump(mode='json') for s in packet.evidence]):return False
        body=record.model_dump(mode='json');signature=body.pop('registration_signature',None)
        return isinstance(signature,str) and hmac.compare_digest(signature,hmac.new(self._key,digest(body).encode(),hashlib.sha256).hexdigest())


def _verify_runtime_files(config,files,evidence_root):
    """Hash manifests transitively, so editing code cannot leave a valid freeze."""
    from backend.research.integrity import safe_path,file_sha256
    roots={'project':Path(__file__).resolve().parents[2],'evidence':Path(evidence_root).resolve()}
    if set(files)!=set(config.artifacts):raise ValueError('Incomplete runtime freeze')
    verified={}
    for name,expected in config.artifacts.items():
        path=Path(files[name]).resolve()
        if not any(path.is_relative_to(root) for root in roots.values()) or file_sha256(path)!=expected:
            raise ValueError('Frozen artifact changed or outside authorized roots')
        verified[str(path)]=expected
        try:manifest=json.loads(path.read_text(encoding='utf-8'))
        except (UnicodeError,ValueError):manifest=None
        if name=='code' and (not isinstance(manifest,dict) or manifest.get('schema')!='freeze-file-set-v1'):
            raise ValueError('Code freeze must enumerate the actual runtime files')
        if isinstance(manifest,dict) and manifest.get('schema')=='freeze-file-set-v1':
            if not manifest.get('files'):raise ValueError('Empty file freeze')
            for item in manifest['files']:
                child=safe_path(roots[item['root']],item['path']);sha=item['sha256']
                if file_sha256(child)!=sha:raise ValueError('Frozen runtime member changed')
                if str(child) in verified and verified[str(child)]!=sha:raise ValueError('Conflicting freeze hashes')
                verified[str(child)]=sha
    return verified


def load_approved_registry(manifest_path,approval_sha256):
    """Explicit local-console bootstrap; cannot be invoked by a web request.

    Source/label files live below the private manifest directory. Code paths are
    relative to the real project, never copies masquerading as loaded modules.
    """
    import re
    from backend.reconstruction.models import Settings
    from backend.research.models import ResearchBundle,Artifact
    from backend.research.integrity import safe_path
    if not isinstance(approval_sha256,str) or not re.fullmatch('[0-9a-f]{64}',approval_sha256):raise ValueError('Explicit approval hash required')
    manifest_path=Path(manifest_path).resolve();root=manifest_path.parent
    data=json.loads(manifest_path.read_text(encoding='utf-8'))
    if data['schema']!='approved-operating-point-v1' or data['origin']!='AUTHENTIC':raise ValueError('Authentic approved package required')
    evidence=safe_path(root,data['evidence_root'])
    project=Path(__file__).resolve().parents[2]
    settings=Settings.model_validate_json(safe_path(root,data['settings_path']).read_text(encoding='utf-8'))
    registry=OperatingRegistry(trusted_approval_digests=(approval_sha256,),feature_provider=FrozenM4Features(settings))
    roots={'project':project,'evidence':evidence}
    files={name:safe_path(roots[item['root']],item['path']) for name,item in data['runtime_files'].items()}
    registry.load(**{key+'_path':safe_path(root,data[key+'_path']) for key in ('development','pre02','validation','pre03')},
        bundle=ResearchBundle.model_validate_json(safe_path(root,data['bundle_path']).read_text(encoding='utf-8')),
        evidence_root=evidence,approval_artifact_id=data['approval_artifact_id'],
        approval_artifact=Artifact.model_validate(data['approval_artifact']),runtime_files=files)
    # The parsed immutable settings are bound above; also invalidate when their
    # on-disk bootstrap inputs change rather than silently loading new authority.
    from backend.research.integrity import file_sha256
    for path in (manifest_path,safe_path(root,data['settings_path']),safe_path(root,data['bundle_path'])):
        registry._runtime_files[str(path)]=file_sha256(path)
    return registry
