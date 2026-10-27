"""M4 hypotheses and unresolved skeletons mapped to unchanged M1 contracts."""
import json
from uuid import NAMESPACE_URL, uuid5

from backend.models.event import (CanonicalEvent, CandidateAlternative, ConstraintValidation,
    EventContent, EventIdentity, EventRevision, ExtractionStatus, FieldObservation, FieldState, RuleTrace)
from backend.reconstruction.models import digest


def canonical_event(group, selection, graph, mode, findings=(), unresolved=False):
    observations, content = [], {}
    ambiguous = False
    for field, domain in group.domains.items():
        options = [n for n in domain if n.state != FieldState.MISSING]
        node = selection.get(field) if selection else None
        alternatives = tuple(CandidateAlternative(raw_value=n.raw or str(n.value),
            normalized_value=json.dumps(n.value,ensure_ascii=False,sort_keys=True) if n.value is not None else None,
            evidence_ids=n.evidence_ids, confidence=n.confidence) for n in options)
        if node and node.state == FieldState.PRESENT:
            content[field] = node.value
            state, raw, refs = FieldState.PRESENT, node.raw, node.evidence_ids
        elif options:
            state, raw, refs = FieldState.UNRESOLVED, None, tuple(sorted({i for n in options for i in n.evidence_ids}))
        else:
            state, raw, refs = FieldState.MISSING, None, ()
        if len(options)>1 or state == FieldState.UNRESOLVED:
            ambiguous = True
        observations.append(FieldObservation(field_name=field,state=state,raw_value=raw,evidence_ids=refs,
            alternatives=alternatives,confidence=node.confidence if node else None,
            transformations=(node.transformations if node else ()) + ('m4-evidence-adapter-v1',),
            justification='Finite-domain hypothesis; alternatives retained' if alternatives else None))
    key = digest({'group':group.group_id,'nodes':[(f,n.node_id) for f,n in sorted((selection or {}).items())],
                  'unresolved_skeleton':unresolved})
    revision = EventRevision(revision_id=uuid5(NAMESPACE_URL,'m4-revision:'+key+mode+graph.settings_digest),
        source_document_id=graph.source.source_document_id,method_id='P1-m4-v1' if mode=='constrained' else 'B3-matched-m4-v1',
        configuration_digest=graph.settings_digest,content=EventContent.model_validate(content),observations=tuple(observations))
    relevant = [f for f in findings if not f.group_ids or group.group_id in f.group_ids]
    status = 'FAILED' if any(f.severity=='HARD' and f.passed is False for f in relevant) else (
        'NOT_ASSESSABLE' if any(f.severity=='HARD' and f.passed is None for f in relevant) else (
        'WARNING' if any(f.passed is False for f in relevant) else 'VALID'))
    validation = ConstraintValidation() if unresolved else ConstraintValidation(status=status,revision_id=revision.revision_id,
        validator_version='m4-catalogue-v1',traces=tuple(RuleTrace(code=f.rule_id,passed=f.passed,severity=f.severity,
                                                               evidence_ids=f.evidence_ids) for f in relevant))
    return CanonicalEvent(candidate_id=key,identity=EventIdentity(event_id=uuid5(NAMESPACE_URL,'m4-local:'+key),
        source_family_id=graph.source.source_family_id,timetable_id=graph.source.timetable_id),revision=revision,
        extraction_status=ExtractionStatus.AMBIGUOUS if ambiguous else (
            ExtractionStatus.EXTRACTED if revision.core_fields_present else ExtractionStatus.PARTIAL),validation=validation)
