"""Conservative application decisions; no probability calibration or threshold fitting."""
from backend.models.event import FieldState
from backend.reconstruction.models import digest
from backend.review.auth import utc_now
from backend.review.models import PolicyAssessment, FieldAssessment, development_policy

MESSAGES={
 'INCOMPLETE_SEARCH':'Reconstruction search did not finish.',
 'EXECUTION_ABORTED':'Reconstruction was aborted and supplied no scorable output.',
 'MISSING_REQUIRED_EVIDENCE':'Required event fields or their evidence are missing.',
 'SOURCE_AMBIGUITY':'Source values are unresolved or conflicting.',
 'CONSTRAINT_VIOLATION':'An applicable hard constraint failed.',
 'CONSTRAINT_NOT_ASSESSED':'An applicable hard constraint remains unresolved.',
 'UNRESOLVED_ALTERNATIVES':'Multiple source interpretations remain.',
 'UNKNOWN_COMPLETENESS':'Full schedule completeness has not been independently established.',
 'PROVISIONAL_SCORING':'M4 support is provisional and is not a calibrated probability.',
 'REVALIDATION_REQUIRED':'The current correction requires validation.',
 'UNAPPROVED_POLICY':'Registered routing settings and their selection evidence are unavailable.',
 'ACCEPT_DISABLED':'Development policy cannot automatically accept events.',
 'UNRESOLVED_SESSION_IDENTITY':'Session identity remains unresolved.',
 'RECURRENCE_UNRESOLVED':'Source recurrence or term bounds remain unresolved.',
 'DUPLICATE_AMBIGUITY':'A possible duplicate or competing target remains unresolved.',
 'REVIEW_FIELD_LIMIT':'Registered bounded-review field count is exceeded.',
 'OMITTED_RECONSTRUCTION':'This is an unresolved skeleton, not a selected prediction.',
 'B3_DIAGNOSTIC_ONLY':'B3 output is retained for diagnostics, not substituted for P1.',
 'APPLICATION_REVIEW_ONLY':'A bounded application review is possible; this is not registered research routing.',
 'INVALID_CORRECTION':'The correction proposal is invalid and was not applied.',
}


def explain(codes):
    return tuple({'code':code,'message':MESSAGES[code]} for code in codes)


def assess_event(run,event,findings=(),*,policy=None,clock=utc_now,revalidation_required=False,invalid_correction=False,validation_dependency_digest=None):
    policy=policy or development_policy()
    reasons={'UNKNOWN_COMPLETENESS','PROVISIONAL_SCORING','UNAPPROVED_POLICY','ACCEPT_DISABLED'}
    hard_block=False
    if run.search_status=='EXECUTION_ABORTED': reasons.add('EXECUTION_ABORTED'); hard_block=True
    group_id=dict(run.candidate_groups).get(event.candidate_id) if event else None
    component=dict(run.component_statuses).get(group_id)
    if component=='SEARCH_INCOMPLETE': reasons.add('INCOMPLETE_SEARCH'); hard_block=True
    if event and event.candidate_id in run.unresolved_ids and event.revision.version==1:
        reasons.add('OMITTED_RECONSTRUCTION'); hard_block=True
    if run.decoder_mode=='unconstrained': reasons.add('B3_DIAGNOSTIC_ONLY'); hard_block=True
    relevant=tuple(f for f in findings if not f.group_ids or group_id in f.group_ids)
    failed=any(f.severity=='HARD' and f.passed is False for f in relevant)
    unknown=any(f.severity=='HARD' and f.passed is None for f in relevant)
    if failed: reasons.add('CONSTRAINT_VIOLATION'); hard_block=True
    if unknown: reasons.add('CONSTRAINT_NOT_ASSESSED'); hard_block=True
    fields=[]
    if event:
        for obs in event.revision.observations:
            needs=obs.state in {FieldState.UNRESOLVED,FieldState.CONFLICTING} or len(obs.alternatives)>1
            fields.append(FieldAssessment(field=obs.field_name,state=obs.state,evidence_count=len(obs.evidence_ids),
                                          alternative_count=len(obs.alternatives),needs_review=needs,parser_confidence=obs.confidence))
            if obs.state in {FieldState.UNRESOLVED,FieldState.CONFLICTING}: reasons.add('SOURCE_AMBIGUITY')
            if len(obs.alternatives)>1: reasons.add('UNRESOLVED_ALTERNATIVES')
        if not event.revision.core_fields_present or not any(obs.evidence_ids for obs in event.revision.observations):
            reasons.add('MISSING_REQUIRED_EVIDENCE'); hard_block=True
        if event.identity.status!='CONFIRMED': reasons.add('UNRESOLVED_SESSION_IDENTITY'); hard_block=True
        if any(f.rule_id=='R01' and f.passed is not True for f in relevant): reasons.add('RECURRENCE_UNRESOLVED'); hard_block=True
        if any(o.field_name=='recurrence' and o.state in {FieldState.UNRESOLVED,FieldState.CONFLICTING} for o in event.revision.observations):
            reasons.add('RECURRENCE_UNRESOLVED'); hard_block=True
        if any(f.rule_id=='DUPLICATE_DIAGNOSTIC' and f.passed is False for f in relevant): reasons.add('DUPLICATE_AMBIGUITY'); hard_block=True
        if sum(f.needs_review for f in fields)>policy.review_max_fields: reasons.add('REVIEW_FIELD_LIMIT'); hard_block=True
        if not {'S01','S02','T01','R01'}<= {f.rule_id for f in relevant}:
            reasons.add('CONSTRAINT_NOT_ASSESSED'); hard_block=True
    else:
        reasons.add('MISSING_REQUIRED_EVIDENCE'); hard_block=True
    if revalidation_required: reasons.add('REVALIDATION_REQUIRED'); hard_block=True
    if invalid_correction: reasons.add('INVALID_CORRECTION'); hard_block=True
    decision='ABSTAIN' if hard_block or not policy.allow_bounded_application_review else 'REVIEW'
    if decision=='REVIEW': reasons.add('APPLICATION_REVIEW_ONLY')
    return PolicyAssessment(candidate_id=event.candidate_id if event else None,event_id=event.identity.event_id if event else None,
        revision_id=event.revision.revision_id if event else None,revision_digest=digest(event.revision.model_dump(mode='json')) if event else None,
        reconstruction_profile=run.profile,decoder_mode=run.decoder_mode,policy_profile=policy.profile_id,policy_version=policy.version,
        policy_digest=digest(policy.model_dump(mode='json')),input_digest=run.input_digest,reconstruction_digest=run.reconstruction_digest,
        assessment_input_digest=digest({'input':run.input_digest,'reconstruction':run.reconstruction_digest,
            'revision':event.revision.model_dump(mode='json') if event else None,
            'findings':[f.model_dump(mode='json') for f in relevant],'validation_dependencies':validation_dependency_digest}),
        decision=decision,reason_codes=tuple(sorted(reasons)),constraint_codes=tuple(sorted({f.rule_id for f in relevant})),findings=relevant,
        fields=tuple(fields),search_status=component or run.search_status,score_provenance='NO_SCORABLE_OUTPUT' if event is None else (
            'M4_SCORE_NOT_REUSED_AFTER_CORRECTION' if event.revision.version>1 else 'M4_PROVISIONAL_SUPPORT_ONLY'),
        base_score_digest=run.base_score_digest,review_required=True,recorded_at=clock())


def assess_run(run,clock=utc_now):
    from backend.review.validation import validation_view
    graph=run.graph()
    if graph is None or not run.events():
        return (assess_event(run,None,clock=clock),)
    _,findings=validation_view(run,run.events(),graph.evidence,graph.context)
    return tuple(assess_event(run,e,findings,clock=clock) for e in run.events())
