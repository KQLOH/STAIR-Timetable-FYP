"""Default operational eligibility stays blocked; only isolated fixture grants write locally."""
from backend.calendar.models import Eligibility,CalendarError
from backend.calendar.mapping import map_event
from backend.reconstruction.models import digest


def assess(bundle,event,*,test_authorized=False):
    reasons=set()
    if bundle.reconstruction_status!='COMPLETE_FINITE_SEARCH': reasons.add('RECONSTRUCTION_NOT_COMPLETE')
    if event.validation.status.value not in {'VALID','WARNING'}: reasons.add('CONSTRAINTS_NOT_VALIDATED')
    if any(t.severity=='HARD' and t.passed is not True for t in event.validation.traces): reasons.add('BLOCKING_CONSTRAINT')
    try: payload=map_event(bundle,event)
    except CalendarError as exc: payload=None; reasons.add(exc.code)
    if not test_authorized:
        reasons.update({'ELIGIBILITY_POLICY_UNAPPROVED','M5_ACCEPT_DISABLED','OPERATIONAL_WRITE_BLOCKED'})
        if event.identity.status!='CONFIRMED': reasons.add('MISSING_STABLE_KEY')
        if event.human_review.status!='APPROVED': reasons.add('REVIEW_NOT_APPROVED')
        if event.human_verification.status=='NOT_VERIFIED': reasons.add('VERIFICATION_NOT_ESTABLISHED')
        decisions=[d for d in bundle.assessments if d.candidate_id==event.candidate_id]
        if not decisions or any(d.revision_id!=event.revision.revision_id for d in decisions): reasons.add('M5_ASSESSMENT_MISSING_OR_STALE')
        if any(not d.registered_routing_eligible for d in decisions): reasons.add('UNAPPROVED_SELECTIVE_POLICY')
    permitted=test_authorized and not reasons
    return Eligibility(candidate_id=event.candidate_id,revision_id=event.revision.revision_id,
        revision_digest=digest(event.revision.model_dump(mode='json')),status='TEST_ELIGIBLE' if permitted else 'BLOCKED',
        sandbox_write_permitted=permitted,authority_kind='ISOLATED_SYNTHETIC_TEST' if test_authorized else 'APPLICATION_BLOCKED',
        reason_codes=tuple(sorted(reasons)),payload=payload)
