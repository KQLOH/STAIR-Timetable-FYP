"""Review the structured TAR UMT capture as supplied; no OCR or search.

Binding proves consistency with the captured source packet, not institutional
authentication. Live retrieval remains the existing FR2 client's responsibility.
"""
import json
from backend.models.event import CanonicalTimetable, EventContent, FieldState, PolicyDecision
from backend.reconstruction.models import Context, FieldNode, EventGroup, Graph, digest, development_settings
from backend.review.models import ReviewError

PROFILE = 'tarumt-structured-review-v1'


def structured_run(packet):
    from backend.review.binding import BoundRun
    from backend.services.event_adapter import CONFIGURATION_DIGEST, LEGACY_CONFIGURATION_DIGEST, _normalize
    from backend.review.validation import validation_view, assessment_for
    if packet.source.route != 'tarumt_html':
        raise ReviewError('STRUCTURED_SOURCE_REQUIRED',422)
    configurations = {e.revision.configuration_digest for e in packet.events}
    if len(configurations) > 1 or configurations - {CONFIGURATION_DIGEST,LEGACY_CONFIGURATION_DIGEST}:
        raise ReviewError('INVALID_STRUCTURED_SOURCE_REVISION',422)
    settings_digest = next(iter(configurations),CONFIGURATION_DIGEST)
    spans = {s.evidence_id:s for s in packet.evidence}
    groups, mapping = [], []
    import html
    for event in packet.events:
        revision = event.revision
        if revision.version != 1 or revision.configuration_digest not in {CONFIGURATION_DIGEST,LEGACY_CONFIGURATION_DIGEST}:
            raise ReviewError('INVALID_STRUCTURED_SOURCE_REVISION',422)
        if event.human_review.status != 'NOT_REVIEWED' or event.human_verification.status != 'NOT_VERIFIED' or event.calendar.approval_status != 'NOT_APPROVED' or event.policy.decision == 'ACCEPT':
            raise ReviewError('STRUCTURED_AUTHORITY_CLAIM_REFUSED',422)
        group_id = 'structured:' + event.candidate_id
        domains = {}
        for observation in revision.observations:
            name = observation.field_name
            value = revision.content.model_dump(mode='json')[name]
            if observation.state == FieldState.PRESENT:
                owned = all(ref in spans and spans[ref].source_document_id == packet.source.source_document_id and spans[ref].route == 'tarumt_html' for ref in observation.evidence_ids)
                text = ' '.join((spans[ref].raw_text + ' ' + (spans[ref].raw_html or '')) for ref in observation.evidence_ids if ref in spans)
                text = ' '.join(html.unescape(text).split()).casefold()
                raw = ' '.join((observation.raw_value or '').split()).casefold()
                if not owned or not raw or raw not in text or digest(_normalize(name,observation.raw_value)) != digest(value):
                    raise ReviewError('STRUCTURED_FIELD_SOURCE_MISMATCH',422)
            key = digest({'candidate':event.candidate_id,'field':name,'revision':str(revision.revision_id)})
            node = FieldNode(candidate_id=key,node_id=key,field=name,value=value,state=observation.state,
                raw=observation.raw_value,evidence_ids=observation.evidence_ids,confidence=observation.confidence,
                shared=name in {'date','day_of_week'},transformations=observation.transformations,
                ownership_units=tuple(ref+':'+name for ref in observation.evidence_ids))
            domains[name] = (node,)
            # Keep all competing source interpretations in constraint scope.
            for i, alternative in enumerate(observation.alternatives):
                if alternative.normalized_value is not None:
                    try: alternative_value=json.loads(alternative.normalized_value)
                    except (ValueError,TypeError): alternative_value=alternative.normalized_value
                    domains[name] += (node.model_copy(update={'node_id':key+':alternative:'+str(i),'value':alternative_value,'state':FieldState.PRESENT}),)
        groups.append(EventGroup(group_id=group_id,evidence_ids=tuple(sorted({r for o in revision.observations for r in o.evidence_ids})),domains=domains))
        mapping.append((event.candidate_id,group_id))
    graph = Graph(source=packet.source,evidence=packet.evidence,groups=tuple(groups),context=Context(),settings_digest=settings_digest,input_issues=packet.issues)
    envelope = {'canonical_output':{'profile_id':PROFILE,'status':'STRUCTURED_SOURCE_VALIDATED','decoder_mode':'constrained',
        'scorable':False,'research_status':'PROVISIONAL_APPLICATION_ONLY','packet':packet.model_dump(mode='json'),
        'unresolved_candidates':[],'findings':[]},'canonical_digest':None}
    run = BoundRun(json.dumps(envelope),digest(packet.model_dump(mode='json')),'',packet.model_dump_json(),graph.model_dump_json(),
        'constrained',PROFILE,'NOT_SEARCHED_STRUCTURED',None,tuple(mapping),tuple(e.model_dump_json() for e in packet.events),(),(), '[]', 'm5-source-review-v1')
    _, findings = validation_view(run,packet.events,packet.evidence,Context())
    validated = packet.model_copy(update={'events':tuple(e.model_copy(update={'validation':assessment_for(e,dict(mapping)[e.candidate_id],findings)}) for e in packet.events)})
    envelope['canonical_output'].update(packet=validated.model_dump(mode='json'),findings=[f.model_dump(mode='json') for f in findings])
    envelope['canonical_digest'] = digest(envelope['canonical_output'])
    from dataclasses import replace
    run = replace(run,original_json=json.dumps(envelope),reconstruction_digest=envelope['canonical_digest'],
        events_json=tuple(e.model_dump_json() for e in validated.events),original_findings_json=json.dumps([f.model_dump(mode='json') for f in findings]))
    return envelope, run


def bind_structured(request):
    if request.policy_profile not in {'m5-development-v1','m5-source-review-v1'}:
        raise ReviewError('REGISTERED_POLICY_UNAVAILABLE',409)
    if request.decoder_mode != 'constrained' or request.reconstruction_context != Context() or request.reconstruction_settings != development_settings() or request.layout_evidence is not None:
        raise ReviewError('UNSUPPORTED_STRUCTURED_REVIEW_CONTEXT',422)
    envelope,run = structured_run(request.source_packet)
    if digest(request.reconstruction) != digest(envelope):
        raise ReviewError('INVALID_STRUCTURED_REVIEW_BINDING',422)
    from dataclasses import replace
    return replace(run,policy_profile=request.policy_profile)


def structured_application(packet):
    from backend.review.policy import assess_run
    from backend.review.api import _decision
    from backend.calendar.models import CalendarInput,Snapshot
    from backend.calendar.eligibility import assess
    from backend.calendar.planning import build_preview
    envelope,run = structured_run(packet)
    decisions = assess_run(run)
    current = CanonicalTimetable.model_validate(envelope['canonical_output']['packet'])
    records = {d.candidate_id:d for d in decisions}
    current = current.model_copy(update={'events':tuple(e.model_copy(update={'policy':PolicyDecision(decision=records[e.candidate_id].decision,
        revision_id=e.revision.revision_id,policy_version=records[e.candidate_id].policy_profile+':1.0',reason_codes=records[e.candidate_id].reason_codes)}) for e in current.events)})
    bundle = CalendarInput(packet=current,reconstruction_status='NOT_PROVIDED',assessments=decisions)
    plan = build_preview(bundle,Snapshot(sandbox_id='diagnostic:no-authorized-target',scope='DEVELOPMENT_BLOCKED',calendar_version=0))
    return {'pipeline':PROFILE,'policyProfile':'m5-source-review-v1','extraction':packet.model_dump(mode='json'),
        'reconstruction':envelope,'reconstruction_json':json.dumps(envelope,ensure_ascii=False,allow_nan=False),
        'policy':[_decision(d) for d in decisions], 'current_packet':current.model_dump(mode='json'),
        'status':'Structured source validated; human review required; calendar writes blocked',
        'stages':{'reconstruction':'STRUCTURED_SOURCE_VALIDATED','policy':'COMPLETE','calendar':'COMPLETE'},
        'calendar':{'eligibility':{'policy_status':'BLOCKED','events':[assess(bundle,e).model_dump(mode='json',exclude={'payload'}) for e in current.events]},
            'preview':{'write_authorized':False,'preview':plan.model_dump(mode='json')}}}
