"""Document-catalogued rules; diagnostics never repair or manufacture source values."""
from datetime import date
from itertools import combinations
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from backend.models.event import EventContent, FieldState
from backend.reconstruction.models import Finding

SHARED = {'S01', 'S02', 'OWNERSHIP'}
ABLATED = {'T01', 'R01', 'C01'}


def values(selection):
    return {key: node.value for key, node in selection.items() if node.state == FieldState.PRESENT}


def minutes(value):
    # Adapted from baseline stair_validator._minutes, without timezone defaults.
    hour, minute = map(int, value.split(':'))
    return 60 * hour + minute


def event_findings(group, selection, graph):
    v = values(selection)
    refs = tuple(sorted({i for n in selection.values() for i in n.evidence_ids}))
    def rule(code, passed, reason, severity='HARD'):
        return Finding(rule_id=code, passed=passed, severity=severity, reason=reason,
                       group_ids=(group.group_id,), evidence_ids=refs)
    core = bool((v.get('course_code') or v.get('course_name')) and
                (v.get('day_of_week') or v.get('date')) and v.get('start_time') and v.get('end_time'))
    result = [rule('S01', core, 'Core identity, day/date and source-local times are required')]
    known = {s.evidence_id for s in graph.evidence}
    valid = all(n.state != FieldState.PRESENT or n.evidence_ids and not set(n.evidence_ids)-known for n in selection.values())
    try:
        EventContent.model_validate(v)
    except (ValidationError, ValueError):
        valid = False
    result.append(rule('S02', bool(valid), 'Canonical vocabulary and field evidence references'))
    temporal = None
    if v.get('start_time') and v.get('end_time') and valid:
        start, end = minutes(v['start_time']), minutes(v['end_time'])
        temporal = 0 <= start < end + 1440 * (v.get('end_day_offset') or 0) <= 2880
        if not v.get('end_day_offset'):
            temporal = 0 <= start < end <= 1440
    result.append(rule('T01', temporal, 'Ordered source-local interval; overnight requires an explicit source marker'))
    # Applicability is determined by source domains, not an optimizer's omissions.
    weekly = any(n.value and isinstance(n.value, dict) and n.value.get('frequency') == 'WEEKLY'
                 for n in group.domains['recurrence'])
    recurrence = v.get('recurrence')
    if weekly:
        term = graph.context.term
        authority_spans = [s for s in graph.evidence if term and s.evidence_id in term.evidence_ids]
        authority_text = ' '.join(s.raw_text for s in authority_spans)
        authority = term and all(s.route == 'authority' for s in authority_spans) and (
            term.start.isoformat() in authority_text and term.end.isoformat() in authority_text)
        if not authority or not recurrence or not recurrence.get('term_start') or not recurrence.get('term_end'):
            recurrence_ok = None
        else:
            begin, end = date.fromisoformat(str(recurrence['term_start'])), date.fromisoformat(str(recurrence['term_end']))
            recurrence_ok = term.start <= begin <= end <= term.end
            for key in ('included_dates', 'excluded_dates', 'replacement_dates'):
                if recurrence.get(key):
                    recurrence_ok = recurrence_ok and all(begin <= date.fromisoformat(str(d)) <= end for d in recurrence[key])
        result.append(rule('R01', recurrence_ok, 'Weekly recurrence requires evidenced bounds and externally verified term authority'))
    else:
        result.append(rule('R01', True, 'No source-visible weekly recurrence; rule not applicable'))
    timezone = v.get('source_timezone')
    timezone_ok = False
    if timezone and graph.context.timezone_authority_ids:
        try:
            ZoneInfo(timezone)
            timezone_ok = all(next(s for s in graph.evidence if s.evidence_id == i).route == 'authority'
                              for i in graph.context.timezone_authority_ids)
            timezone_ok = timezone_ok and any(timezone in s.raw_text for s in graph.evidence
                                               if s.evidence_id in graph.context.timezone_authority_ids)
        except ZoneInfoNotFoundError:
            pass
    result.append(rule('T02', timezone_ok, 'Export gate only: authoritative timezone required; no calendar eligibility granted', 'INFO'))
    if v.get('teaching_weeks') and v.get('excluded_weeks') and not set(v['excluded_weeks']) <= set(v['teaching_weeks']):
        result.append(rule('WEEK_DIAGNOSTIC', False, 'Excluded weeks extend beyond evidenced teaching weeks', 'INFO'))
    if v.get('date') and v.get('day_of_week') and date.fromisoformat(str(v['date'])).isoweekday() != v['day_of_week']:
        result.append(rule('DATE_DAY_DIAGNOSTIC', False, 'Source date and weekday disagree', 'INFO'))
    return result


def _source_unique(group, field):
    options = {str(n.value) for n in group.domains[field] if n.state == FieldState.PRESENT}
    return next(iter(options)) if len(options) == 1 else None


def _source_value(group, field):
    options = [n.value for n in group.domains[field] if n.state == FieldState.PRESENT]
    return options[0] if options and all(v == options[0] for v in options) else None


def timetable_findings(assignments, graph):
    result = []
    units = {}
    for group, selected in assignments:
        for node in selected.values():
            if node.shared or node.state != FieldState.PRESENT:
                continue
            for unit in node.ownership_units:
                units.setdefault(unit, []).append((group.group_id, node.field))
    conflicts = sorted({g for owners in units.values() if len(set(owners)) > 1 for g, _ in owners})
    result.append(Finding(rule_id='OWNERSHIP', passed=not conflicts, severity='HARD',
                          reason='Exclusive source nodes cannot support incompatible simultaneous assignments', group_ids=tuple(conflicts)))
    for (g1, s1), (g2, s2) in combinations(assignments, 2):
        a, b = values(s1), values(s2)
        refs = tuple(sorted(set(g1.evidence_ids + g2.evidence_ids)))
        def emit(code, passed, reason, severity='HARD'):
            result.append(Finding(rule_id=code, passed=passed, severity=severity, reason=reason,
                                  group_ids=(g1.group_id,g2.group_id), evidence_ids=refs))
        key = ('course_code','course_name','session_type','section','cohort_id','date','day_of_week',
               'start_time','end_time','teaching_weeks','excluded_weeks')
        if (a.get('course_code') or a.get('course_name')) and all(a.get(k) == b.get(k) for k in key):
            emit('DUPLICATE_DIAGNOSTIC', False, 'Possible duplicate session across distinct source regions; no automatic deletion', 'INFO')
        cohort1, cohort2 = _source_unique(g1,'cohort_id'), _source_unique(g2,'cohort_id')
        if cohort1 and cohort2 and cohort1 != cohort2:
            emit('C01', True, 'Distinct evidenced cohorts'); continue
        alternative = set(graph.context.alternative_group_evidence_ids)
        if alternative and set(g1.evidence_ids) <= alternative and set(g2.evidence_ids) <= alternative:
            emit('C01', True, 'Caller-supplied, evidence-backed alternative grouping'); continue
        section1, section2 = _source_unique(g1,'section'), _source_unique(g2,'section')
        if section1 and section2 and section1 != section2:
            emit('C01', None, 'Distinct sections require cohort membership evidence; no hard overlap inference'); continue
        # Omitting an observed conditional cannot erase its constraint scope.
        weeks1 = a.get('teaching_weeks') or _source_value(g1,'teaching_weeks')
        weeks2 = b.get('teaching_weeks') or _source_value(g2,'teaching_weeks')
        active1 = set(weeks1 or ()) - set(a.get('excluded_weeks') or _source_value(g1,'excluded_weeks') or ())
        active2 = set(weeks2 or ()) - set(b.get('excluded_weeks') or _source_value(g2,'excluded_weeks') or ())
        if weeks1 and weeks2 and not active1 & active2:
            emit('C01', True, 'Disjoint evidenced teaching weeks'); continue
        date1,date2 = a.get('date') or _source_value(g1,'date'),b.get('date') or _source_value(g2,'date')
        day1,day2 = a.get('day_of_week') or _source_value(g1,'day_of_week'),b.get('day_of_week') or _source_value(g2,'day_of_week')
        same_date = bool(date1 and date1 == date2)
        same_weekday = bool(day1 and day1 == day2)
        comparable = same_date or same_weekday and bool(active1 & active2)
        if not (cohort1 and cohort1 == cohort2 and comparable):
            emit('C01', None, 'Insufficient cohort or occurrence evidence for a hard overlap finding'); continue
        if all(v.get('start_time') and v.get('end_time') for v in (a,b)) and not a.get('end_day_offset') and not b.get('end_day_offset'):
            # Baseline overlap_checker interval predicate, scoped to evidenced occurrences.
            overlap = max(minutes(a['start_time']),minutes(b['start_time'])) < min(minutes(a['end_time']),minutes(b['end_time']))
            emit('C01', not overlap, 'Same evidenced cohort and occurrence; source-local intervals overlap' if overlap else 'No same-cohort interval overlap')
        else:
            emit('C01', None, 'Overnight/partial overlap needs explicit occurrence expansion')
    result.append(Finding(rule_id='COMPLETENESS', passed=None, severity='INFO',
                          reason='Source-first complete-schedule verification has not been performed', group_ids=()))
    return result


def feasible(findings, mode):
    return all(f.passed is True for f in findings if f.rule_id in SHARED) and (
        mode == 'unconstrained' or all(f.passed is True or f.rule_id == 'C01' and f.passed is None
                                      for f in findings if f.rule_id in ABLATED))
