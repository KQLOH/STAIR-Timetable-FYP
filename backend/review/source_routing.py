"""Deterministic application triage, never calibrated probability or ACCEPT authority."""
import json
from backend.models.event import FieldState, DecisionStatus
from backend.reconstruction.models import digest
from backend.review.models import SourceRouting

PROFILE = 'm5-source-review-v1'
RULES = {'profile': PROFILE, 'version': '1.0', 'accept_enabled': False,
    'mandatory': ['course_code_or_name', 'day_or_date', 'start_time', 'end_time'],
    'critical_rules': ['S01', 'S02', 'T01', 'R01', 'OWNERSHIP', 'C01'],
    'unique_source_conflict_check': True, 'unknown_cohort_action': 'REVIEW',
    'optional_review_fields': ['location','session_type','date'],
    'confidence_thresholds': None, 'reference_metrics': 'NOT_ESTABLISHED'}
CONFIG_DIGEST = digest(RULES)
MESSAGES = {
    'SOURCE_SUPPORTED': ('The class has linked source evidence and passed the assessed event checks.', 'Check the class against the original timetable.'),
    'MISSING_CORE_EVIDENCE': ('Required class identity, day/date or time evidence is missing.', 'Upload a clearer or more complete source; inspect the original before reconstructing this class.'),
    'CORE_READING_AMBIGUOUS': ('The source supports competing readings of a required class detail.', 'Resolve the identity or time ambiguity against the original source.'),
    'UNOWNED_FIELD_EVIDENCE': ('A displayed field is not supported by evidence from this source.', 'Refresh the source and its current revision; do not use this candidate.'),
    'FIELD_CHECK_REQUIRED': ('Some class details are incomplete or ambiguous.', 'Inspect the original and correct the listed detail where supported.'),
    'CALENDAR_DATE_REQUIRED': ('A weekday is known, but specific calendar dates are not confirmed.', 'Confirm the dates or term from the original; weekday alone cannot authorize a calendar action.'),
    'RECURRENCE_EVIDENCE_INCOMPLETE': ('A stated repeat schedule cannot be safely established.', 'Supply the original term, teaching-week and exception evidence.'),
    'STRUCTURE_CHECK_REQUIRED': ('Source grouping or table structure remains uncertain.', 'Inspect which source cell or lines belong to this class.'),
    'CONFLICT_SCOPE_UNCONFIRMED': ('The group or cohort relationship needed for a schedule-conflict check is not confirmed.', 'Check the relevant groups and overlapping sessions; calendar eligibility remains blocked.'),
    'CONSTRAINT_FAILURE': ('An assessed schema, time, ownership or conflict check failed.', 'Resolve the reported source conflict; do not use this candidate as a complete event.'),
    'CONSTRAINT_CHECK_INCOMPLETE': ('Required event checks could not be completed.', 'Provide the missing source context and run extraction or revalidation again.'),
    'DUPLICATE_REQUIRES_RESOLUTION': ('This candidate may duplicate or compete with another class.', 'Compare both source sessions before keeping either candidate.'),
    'SOURCE_DAY_DATE_CONFLICT': ('The printed weekday and date disagree.', 'Check the original weekday and calendar date.'),
    'REVALIDATION_REQUIRED': ('The current edit has not completed its required checks.', 'Check the saved class before using its routing result.'),
    'INCOMPLETE_RECONSTRUCTION': ('This candidate was not safely selected by a complete constrained reconstruction.', 'Inspect the source or retry with the appropriate manual reader.'),
    'REGISTERED_ACCEPT_UNAVAILABLE': ('Registered ACCEPT is unavailable; source support is not calibrated reliability.', 'Human checking, independent verification and calendar approval remain separate.')}


def source_conflicts(graph):
    """Read-only checks over unique source values, including omitted candidates.

    This does not create predictions or choose between alternative field values.
    It prevents an omitted conflicting source session from hiding a proven clash.
    """
    from backend.reconstruction.constraints import timetable_findings
    assignments=[]
    for group in graph.groups:
        selected={}
        for field,nodes in group.domains.items():
            present=[node for node in nodes if node.state==FieldState.PRESENT]
            distinct={digest(node.value) for node in present}
            if len(distinct)==1:selected[field]=present[0]
            else:
                empty=next((node for node in nodes if node.state==FieldState.MISSING),nodes[-1])
                selected[field]=empty.model_copy(update={'state':FieldState.MISSING,'value':None})
        if all(selected[name].state==FieldState.PRESENT for name in ('start_time','end_time')):
            assignments.append((group,selected))
    return tuple(f for f in timetable_findings(assignments,graph) if f.rule_id in {'C01','DUPLICATE_DIAGNOSTIC'} and f.passed is False)


def assess_source(run, event, findings=(), *, source_evidence=None, pending=False, source_graph=None, source_findings=None):
    graph = source_graph if source_graph is not None else run.graph()
    evidence = tuple(source_evidence if source_evidence is not None else graph.evidence if graph else ())
    spans = {span.evidence_id: span for span in evidence}
    reasons, fields, refs, unsafe = set(), set(), set(), False
    def add(code, field=None, block=False):
        nonlocal unsafe
        reasons.add(code)
        if field: fields.add(field)
        unsafe = unsafe or block
    group_id = dict(run.candidate_groups).get(event.candidate_id) if event else None
    extra = source_findings if source_findings is not None else source_conflicts(graph) if graph else ()
    relevant = tuple(f for f in (*findings,*extra) if not f.group_ids or group_id in f.group_ids)
    component = dict(run.component_statuses).get(group_id)
    if pending: add('REVALIDATION_REQUIRED', block=True)
    if not event or not graph or run.decoder_mode != 'constrained' or run.search_status == 'EXECUTION_ABORTED' or component == 'SEARCH_INCOMPLETE':
        add('INCOMPLETE_RECONSTRUCTION', block=True)
    if event and event.candidate_id in run.unresolved_ids and event.revision.version == 1:
        add('INCOMPLETE_RECONSTRUCTION', block=True)
    observations = {o.field_name: o for o in event.revision.observations} if event else {}
    content = event.revision.content if event else None
    def competing(obs):
        values = set()
        if obs.state == FieldState.PRESENT:
            values.add(digest(content.model_dump(mode='json')[obs.field_name]))
        for alternative in obs.alternatives:
            try: value = json.loads(alternative.normalized_value) if alternative.normalized_value is not None else alternative.raw_value
            except (ValueError,TypeError): value = alternative.normalized_value or alternative.raw_value
            values.add(digest(value))
        return len(values) > 1
    def clear(name):
        obs = observations.get(name)
        return bool(obs and obs.state == FieldState.PRESENT and obs.evidence_ids and not competing(obs))
    for alternatives in (('course_code','course_name'), ('day_of_week','date'), ('start_time',), ('end_time',)):
        if not any(clear(name) for name in alternatives):
            ambiguous = any(observations.get(name) and (competing(observations[name]) or observations[name].state in {FieldState.CONFLICTING,FieldState.UNRESOLVED}) for name in alternatives)
            add('CORE_READING_AMBIGUOUS' if ambiguous else 'MISSING_CORE_EVIDENCE', alternatives[0], True)
    for name, obs in observations.items():
        refs.update(obs.evidence_ids)
        if obs.state == FieldState.PRESENT:
            if not obs.evidence_ids or any(ref not in spans or spans[ref].source_document_id != event.revision.source_document_id or not spans[ref].raw_text.strip() for ref in obs.evidence_ids):
                add('UNOWNED_FIELD_EVIDENCE', name, True)
        if obs.state in {FieldState.CONFLICTING,FieldState.UNRESOLVED} or competing(obs):
            add('FIELD_CHECK_REQUIRED', name)
    if content:
        if not content.date: add('CALENDAR_DATE_REQUIRED','date')
        for name in ('location','session_type'):
            if not clear(name): add('FIELD_CHECK_REQUIRED',name)
    required = {'S01','S02','T01','R01'}
    if not required <= {f.rule_id for f in relevant}: add('CONSTRAINT_CHECK_INCOMPLETE', block=True)
    for finding in relevant:
        if finding.rule_id == 'C01' and finding.passed is None:
            add('CONFLICT_SCOPE_UNCONFIRMED','cohort_id')
            continue
        if finding.rule_id in RULES['critical_rules'] and finding.severity == 'HARD' and finding.passed is not True:
            add('CONSTRAINT_FAILURE' if finding.passed is False else 'CONSTRAINT_CHECK_INCOMPLETE', block=True)
        if finding.rule_id == 'R01' and finding.passed is not True: add('RECURRENCE_EVIDENCE_INCOMPLETE','recurrence',True)
        if finding.rule_id == 'DUPLICATE_DIAGNOSTIC' and finding.passed is False: add('DUPLICATE_REQUIRES_RESOLUTION',block=True)
        if finding.rule_id == 'DATE_DAY_DIAGNOSTIC' and finding.passed is False: add('SOURCE_DAY_DATE_CONFLICT','date')
    # Retain source-visible layout cautions without guessing a new layout or
    # treating unavailable OCR confidence as an empirical failure probability.
    if graph:
        structure = [str(code) for code in graph.diagnostics]
        try:
            layout = (json.loads(run.original_json).get('canonical_output') or {}).get('layout') or {}
            for issue in layout.get('issues', []):
                if isinstance(issue, dict) and (not issue.get('evidence_ids') or set(issue['evidence_ids']) & refs): structure.append(issue.get('code',''))
            for region in layout.get('regions', []):
                owner = 'region:' + region.get('region_id','')
                if any(spans[ref].locator == owner for ref in refs if ref in spans): structure.extend(region.get('issues', []))
        except (ValueError,TypeError,AttributeError): add('STRUCTURE_CHECK_REQUIRED')
        if any('AUTHORITY' not in code and any(word in code for word in ('AMBIGUOUS','UNSUPPORTED','UNCONFIRMED','NON_RECTANGULAR','COMPETING','UNRESOLVED_AXIS')) for code in structure): add('STRUCTURE_CHECK_REQUIRED')
    substantive = set(reasons)
    if not substantive: add('SOURCE_SUPPORTED')
    reasons.add('REGISTERED_ACCEPT_UNAVAILABLE')
    route = 'ABSTAIN' if unsafe else 'REVIEW'
    support = 'INSUFFICIENT' if unsafe else 'NEEDS_CHECK' if substantive else 'SOURCE_SUPPORTED'
    return SourceRouting(profile=PROFILE, configuration_digest=CONFIG_DIGEST, route=route, support=support,
        revision_id=event.revision.revision_id if event else None,
        revision_digest=digest(event.revision.model_dump(mode='json')) if event else None,
        reason_codes=tuple(sorted(reasons)), fields_to_check=tuple(sorted(fields)), evidence_ids=tuple(sorted(refs)),
        explanations=tuple({'code':code,'message':MESSAGES[code][0],'action':MESSAGES[code][1]} for code in sorted(reasons)))


def apply_source_policy(record):
    routing = record.source_routing
    if routing is None: return record
    return record.model_copy(update={'decision':DecisionStatus(routing.route), 'policy_profile':PROFILE, 'policy_version':'1.0',
        'policy_digest':CONFIG_DIGEST, 'reason_codes':routing.reason_codes,
        'assessment_input_digest':digest({'assessment':record.assessment_input_digest,'source_rules':CONFIG_DIGEST}),
        'review_required':True})


def report(decisions):
    events = [d for d in decisions if d.candidate_id is not None]
    registered=[d for d in events if d.registration_mode is not None]
    route=lambda d: d.decision.value if d.registration_mode else d.source_routing.route if d.source_routing else None
    counts = {name:sum(route(d)==name for d in events) for name in ('ACCEPT','REVIEW','ABSTAIN')}
    total = len(events)
    return {'profile':PROFILE,'population':'ALL_RECONSTRUCTED_CANDIDATES_INCLUDING_UNSELECTED',
        'candidate_count':total,'run_level_abstentions':sum(d.candidate_id is None for d in decisions),
        'registered_accept_count':counts['ACCEPT'],'automatic_coverage':counts['ACCEPT']/total if total else None,
        'review_count':counts['REVIEW'],'review_demand':counts['REVIEW']/total if total else None,
        'abstain_count':counts['ABSTAIN'],'abstention_rate':counts['ABSTAIN']/total if total else None,
        'source_supported_count':sum(d.source_routing is not None and d.source_routing.support=='SOURCE_SUPPORTED' for d in events),
        'registered_policy_status':('TEST_ONLY_REGISTERED' if any(d.registration_mode=='TEST_ONLY' for d in registered) else 'AUTHENTIC_REGISTERED') if registered else 'DISABLED_VALIDATION_EVIDENCE_UNAVAILABLE','selective_effectiveness':'NOT_ESTABLISHED',
        'supported_event_accuracy':None,'unsafe_acceptance':None,'reference_evaluation':'NOT_EVALUATED',
        'note':'Routing counts are software behavior, not extraction accuracy or proof of selective safety.'}
