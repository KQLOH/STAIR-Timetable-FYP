"""Revalidate human revisions without re-decoding or changing registered algorithms."""
from backend.models.event import (CanonicalTimetable, FieldState, ConstraintValidation, RuleTrace)
from backend.reconstruction.constraints import event_findings, timetable_findings
from backend.reconstruction.models import FieldNode, EventGroup, digest


def validate_context(context, evidence):
    known={s.evidence_id for s in evidence}
    refs=set(context.timezone_authority_ids+context.alternative_group_evidence_ids)
    for link in context.links:
        refs.update((link.source_evidence_id,link.target_evidence_id,*link.reason_evidence_ids))
    if context.term: refs.update(context.term.evidence_ids)
    if refs-known:
        raise ValueError('Unknown relationship evidence')


def validation_view(run, events, evidence, context):
    graph=run.graph()
    validate_context(context,evidence)
    group_ids=dict(run.candidate_groups)
    groups=[]; assignments=[]
    for event in events:
        original=next(g for g in graph.groups if g.group_id==group_ids[event.candidate_id])
        nodes={}
        for obs in event.revision.observations:
            value=event.revision.content.model_dump(mode='json')[obs.field_name]
            key=digest({'revision':str(event.revision.revision_id),'field':obs.field_name,'value':value,'refs':obs.evidence_ids})
            ownership=[]
            if obs.raw_value:
                for ref in obs.evidence_ids:
                    span=next(s for s in evidence if s.evidence_id==ref)
                    offset=span.raw_text.lower().find(obs.raw_value.lower())
                    if offset>=0: ownership.append(f'{ref}:{offset}:{offset+len(obs.raw_value)}')
            shared=any(t in {'source-header-scope','same-cell-course-context','explicit-source-context-link'} for t in obs.transformations)
            if obs.origin!='human':
                matching=[n for n in original.domains[obs.field_name] if n.evidence_ids==obs.evidence_ids
                          and n.raw==obs.raw_value and n.transformations+('m4-evidence-adapter-v1',)==obs.transformations]
                if matching:
                    shared=all(n.shared for n in matching)
                    ownership=list(matching[0].ownership_units)
            node=FieldNode(candidate_id=key,node_id=key,field=obs.field_name,value=value,state=obs.state,
                           raw=obs.raw_value,evidence_ids=obs.evidence_ids,ownership_units=tuple(ownership),shared=shared,
                           confidence=obs.confidence,transformations=obs.transformations)
            nodes[obs.field_name]=node
        observations={o.field_name:o for o in event.revision.observations}
        # Preserve original source applicability until a field is explicitly corrected.
        domains={field:(node,) if 'm5-human-correction-v1' in observations[field].transformations
                 else original.domains[field] for field,node in nodes.items()}
        group=EventGroup(group_id=group_ids[event.candidate_id],evidence_ids=tuple(sorted({i for n in nodes.values() for i in n.evidence_ids})),
                         domains=domains)
        groups.append(group); assignments.append((group,nodes))
    graph=graph.model_copy(update={'groups':tuple(groups),'evidence':tuple(evidence),'context':context})
    findings=[]
    for group,nodes in assignments:
        findings.extend(event_findings(group,nodes,graph))
    findings.extend(timetable_findings([(g,n) for g,n in assignments if all(n[f].state==FieldState.PRESENT for f in ('start_time','end_time'))],graph))
    return graph,tuple(findings)


def assessment_for(event,group_id,findings):
    relevant=tuple(f for f in findings if not f.group_ids or group_id in f.group_ids)
    state='FAILED' if any(f.severity=='HARD' and f.passed is False for f in relevant) else (
        'NOT_ASSESSABLE' if any(f.severity=='HARD' and f.passed is None for f in relevant) else (
        'WARNING' if any(f.passed is False for f in relevant) else 'VALID'))
    return ConstraintValidation(status=state,revision_id=event.revision.revision_id,validator_version='m5-source-revalidation-v1',
        traces=tuple(RuleTrace(code=f.rule_id,passed=f.passed,severity=f.severity,evidence_ids=f.evidence_ids) for f in relevant))
