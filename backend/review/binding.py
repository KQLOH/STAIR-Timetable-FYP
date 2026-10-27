"""Bind checksummed M4 development output to its source, settings and candidates.

Checksums prove consistency, not who executed an algorithm. External API input
is explicitly caller-supplied; trusted-host ingestion establishes the service boundary.
"""
from dataclasses import dataclass
import json
import re
from typing import Any, Literal

from pydantic import Field
from backend.models.evidence import ContractModel
from backend.models.event import CanonicalTimetable, CanonicalEvent, EventContent, FieldState
from backend.reconstruction.adapter import canonical_event
from backend.reconstruction.candidates import build_graph
from backend.reconstruction.models import Context, Settings, Graph, Finding, development_settings, digest, canonical_bytes
from backend.review.models import ReviewError


class AssessmentInput(ContractModel):
    source_packet: CanonicalTimetable
    reconstruction: dict[str, Any]
    decoder_mode: Literal['constrained','unconstrained'] = 'constrained'
    reconstruction_context: Context = Field(default_factory=Context)
    reconstruction_settings: Settings = Field(default_factory=development_settings)
    policy_profile: Literal['m5-development-v1','registered'] = 'm5-development-v1'
    # Separate application evidence only; original M4 and registered algorithms unchanged.
    layout_evidence: dict[str,Any] | None = None


def recover_selection(group,event,graph,mode):
    selected={}
    for observation in event.revision.observations:
        choices=[n for n in group.domains[observation.field_name]
                 if n.transformations+('m4-evidence-adapter-v1',)==observation.transformations
                 and n.confidence==observation.confidence]
        if observation.state==FieldState.PRESENT:
            choices=[n for n in choices if n.state==FieldState.PRESENT and digest(EventContent.model_validate({n.field:n.value}).model_dump(mode='json')[n.field])==digest(event.revision.content.model_dump(mode='json')[n.field])
                     and n.raw==observation.raw_value and n.evidence_ids==observation.evidence_ids]
        if not choices:
            return None
        selected[observation.field_name]=min(choices,key=lambda n:n.node_id)
    expected=canonical_event(group,selected,graph,mode)
    if expected.candidate_id==event.candidate_id and expected.revision==event.revision and expected.identity==event.identity:
        return selected
    return None


@dataclass(frozen=True)
class BoundRun:
    original_json: str
    input_digest: str
    reconstruction_digest: str
    source_json: str
    graph_json: str | None
    decoder_mode: str
    profile: str
    search_status: str
    base_score_digest: str | None
    candidate_groups: tuple[tuple[str,str],...]
    events_json: tuple[str,...]
    unresolved_ids: tuple[str,...]
    component_statuses: tuple[tuple[str,str],...]
    original_findings_json: str

    def graph(self):
        return Graph.model_validate_json(self.graph_json) if self.graph_json else None

    def events(self):
        return tuple(CanonicalEvent.model_validate_json(e) for e in self.events_json)


def bind_input(request):
    # Detach caller-owned mutable dictionaries before any stateful ingestion.
    request=AssessmentInput.model_validate_json(request.model_dump_json())
    if len(request.source_packet.evidence)>512 or sum(len(s.raw_text) for s in request.source_packet.evidence)>500000:
        raise ReviewError('EVIDENCE_LIMIT',413)
    profile=(request.reconstruction.get('canonical_output') or {}).get('profile_id')
    if profile in {'ocr-layout-m52-v1','ocr-ruled-grid-s2a-v1','ocr-block-grid-s2b-v1'}:
        from backend.review.layout_binding import bind_layout_review
        return bind_layout_review(request,profile)
    if request.layout_evidence is not None:
        raise ReviewError('UNEXPECTED_REVIEW_LAYOUT_EVIDENCE',422)
    if request.source_packet.source.route=='tarumt_html':
        raise ReviewError('STRUCTURED_REVIEW_BINDING_NOT_APPROVED',409)
    if request.reconstruction_settings.profile_id!='m4-development-v1' or request.reconstruction_settings.algorithm!='finite-assignment-search-m4-v1':
        raise ReviewError('UNSUPPORTED_RECONSTRUCTION_PROFILE',422)
    if request.policy_profile=='registered':
        raise ReviewError('REGISTERED_POLICY_UNAVAILABLE',409)
    envelope=request.reconstruction
    original_json=json.dumps(envelope,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)+'\n'
    canonical=envelope.get('canonical_output')
    input_digest=digest({'packet':request.source_packet.model_dump(mode='json'),
                         'context':request.reconstruction_context.model_dump(mode='json'),
                         'settings':request.reconstruction_settings.model_dump(mode='json'),'mode':request.decoder_mode})
    if canonical is None:
        if envelope.get('status')!='EXECUTION_ABORTED' or envelope.get('scorable') is not False or envelope.get('reason')!='WALL_CLOCK_WATCHDOG':
            raise ReviewError('INVALID_RECONSTRUCTION_PROVENANCE',422)
        return BoundRun(original_json,input_digest,digest(envelope),request.source_packet.model_dump_json(),None,
                        request.decoder_mode,request.reconstruction_settings.profile_id,'EXECUTION_ABORTED',None,(),(),(),(),'[]')
    try:
        if envelope.get('canonical_digest')!=digest(canonical):
            raise ValueError('digest mismatch')
        if (canonical['schema_version']!='m4.0' or canonical['decoder_mode']!=request.decoder_mode or
            canonical['research_status']!='PROVISIONAL_APPLICATION_ONLY' or canonical['scorable'] is not False or
            canonical['profile_id']!=request.reconstruction_settings.profile_id or
            canonical['method_id']!=('P1-m4-v1' if request.decoder_mode=='constrained' else 'B3-matched-m4-v1')):
            raise ValueError('unsupported reconstruction')
        graph=build_graph(request.source_packet,request.reconstruction_settings,request.reconstruction_context)
        if canonical['candidate_graph_digest']!=digest(graph.model_dump(mode='json')) or digest(canonical['graph'])!=digest(graph.model_dump(mode='json')):
            raise ValueError('graph/source mismatch')
        packet=CanonicalTimetable.model_validate(canonical['packet'])
        if packet.complete_schedule_verification.status!='NOT_VERIFIED' or canonical['complete_schedule_verified'] is not False:
            raise ValueError('unexpected schedule attestation')
        if packet.source!=graph.source or packet.evidence!=graph.evidence:
            raise ValueError('source/evidence mismatch')
        skeletons=tuple(CanonicalEvent.model_validate(e) for e in canonical['unresolved_candidates'])
        findings=tuple(Finding.model_validate(f) for f in canonical['findings'])
        known={s.evidence_id for s in graph.evidence}; gids={g.group_id for g in graph.groups}
        if any(set(f.evidence_ids)-known or set(f.group_ids)-gids for f in findings):
            raise ValueError('unknown finding reference')
        components=canonical['components']
        statuses={gid:c['status'] for c in components for gid in c['group_ids']}
        if set(statuses)!=gids or sum(len(c['group_ids']) for c in components)!=len(gids) or any(s not in {'OPTIMAL_IN_RETAINED_DOMAIN','SEARCH_INCOMPLETE'} for s in statuses.values()):
            raise ValueError('invalid search ledger')
        status='SEARCH_INCOMPLETE' if 'SEARCH_INCOMPLETE' in statuses.values() else 'COMPLETE_FINITE_SEARCH'
        if canonical['status']!=status:
            raise ValueError('contradictory search status')
        mapped=[]
        for e in packet.events+skeletons:
            match=None
            for group in graph.groups:
                if e in skeletons:
                    expected=canonical_event(group,None,graph,request.decoder_mode,unresolved=True)
                    if e.candidate_id==expected.candidate_id and e.revision==expected.revision and e.identity==expected.identity:
                        match=group.group_id
                elif recover_selection(group,e,graph,request.decoder_mode) is not None:
                    match=group.group_id
                if match: break
            if match is None or e.revision.configuration_digest!=graph.settings_digest:
                raise ValueError('candidate not bound to source domains')
            if e in packet.events and statuses[match]=='SEARCH_INCOMPLETE':
                raise ValueError('incomplete component prediction')
            # No external attestation/permission may be smuggled through M4 input.
            if (e.policy.decision is not None or e.human_review.status!='NOT_REVIEWED' or
                e.human_verification.status!='NOT_VERIFIED' or e.calendar.eligibility!='NOT_ASSESSED' or
                e.calendar.approval_status!='NOT_APPROVED' or e.calendar.sync_status!='NOT_SUBMITTED'):
                raise ValueError('unexpected promoted state')
            mapped.append((e.candidate_id,match))
        if len(mapped)!=len(gids) or len({g for _,g in mapped})!=len(gids):
            raise ValueError('missing or duplicate candidate group')
        if not isinstance(canonical['base_score_digest'],str) or not re.fullmatch(r'[0-9a-f]{64}',canonical['base_score_digest']):
            raise ValueError('missing score provenance')
    except (KeyError,TypeError,ValueError,StopIteration):
        raise ReviewError('INVALID_RECONSTRUCTION_PROVENANCE',422) from None
    return BoundRun(original_json,input_digest,envelope['canonical_digest'],request.source_packet.model_dump_json(),graph.model_dump_json(),
                    request.decoder_mode,canonical['profile_id'],status,canonical['base_score_digest'],tuple(mapped),
                    tuple(e.model_dump_json() for e in packet.events+skeletons),tuple(e.candidate_id for e in skeletons),
                    tuple(sorted(statuses.items())),canonical_bytes([f.model_dump(mode='json') for f in findings]).decode())
