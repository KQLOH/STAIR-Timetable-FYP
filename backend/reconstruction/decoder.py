"""Deterministic exhaustive finite assignment search shared by P1 and matched B3.

Shared S01 partial pruning only. Both modes visit/evaluate the same retained domain;
constraints alter feasibility/objective, never traversal or resource accounting.
"""
from collections import Counter
from itertools import product
import time

from backend.models.event import CanonicalTimetable, FieldState
from backend.models.evidence import ParseIssue
from backend.reconstruction.adapter import canonical_event
from backend.reconstruction.constraints import event_findings, timetable_findings, feasible, SHARED
from backend.reconstruction.models import digest
from backend.reconstruction.scoring import score, objective, applicable, soft_findings


class SearchIncomplete(Exception):
    pass


class ExecutionAborted(Exception):
    pass


class Budget:
    def __init__(self, settings, clock, started):
        self.settings, self.clock = settings, clock
        self.started = started
        self.assignments = self.operations = 0

    def charge(self, assignments=0, operations=1):
        if self.clock()-self.started >= self.settings.watchdog_seconds:
            raise ExecutionAborted('WALL_CLOCK_WATCHDOG')
        self.assignments += assignments
        self.operations += operations
        if self.assignments > self.settings.max_assignments:
            raise SearchIncomplete('ASSIGNMENT_CAP')
        if self.operations > self.settings.max_operations:
            raise SearchIncomplete('OPERATION_CAP')


def components(graph):
    """Conservative ownership/cohort dependency components, without source-label access."""
    parent = list(range(len(graph.groups)))
    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i
    def merge(i,j):
        parent[root(j)] = root(i)
    scopes = {}
    for i, group in enumerate(graph.groups):
        keys = {('cohort',str(n.value)) for n in group.domains['cohort_id'] if n.state==FieldState.PRESENT}
        keys |= {('ownership',unit) for domain in group.domains.values() for n in domain if not n.shared for unit in n.ownership_units}
        for key in sorted(keys):
            if key in scopes:
                merge(scopes[key],i)
            else:
                scopes[key]=i
    result={}
    for i, group in enumerate(graph.groups):
        result.setdefault(root(i),[]).append(group)
    return sorted(result.values(),key=lambda groups:groups[0].group_id)


def _partial_core(selection):
    for fields in (('course_code','course_name'),('date','day_of_week'),('start_time',),('end_time',)):
        if all(f in selection for f in fields) and not any(selection[f].state==FieldState.PRESENT for f in fields):
            return False
    return True


def _options(group,graph,settings,budget):
    keys=sorted(group.domains)
    options=[]
    def visit(index,selection):
        if index==len(keys):
            findings=event_findings(group,selection,graph)
            record=score(group,selection,graph,settings)
            findings.extend(soft_findings(group,record,settings))
            budget.charge(operations=len(findings)+len(group.relations)+len(keys))
            if all(f.passed is True for f in findings if f.rule_id in SHARED):
                tie=tuple(selection[k].node_id for k in keys)
                options.append((dict(selection),record,findings,tie))
            return
        field=keys[index]
        for node in group.domains[field]:
            budget.charge(assignments=1)
            selection[field]=node
            if _partial_core(selection):
                visit(index+1,selection)
        selection.pop(field,None)
    visit(0,{})
    options.sort(key=lambda item:item[3])
    # OMIT is a real finite-domain choice, last in lexicographic tie ordering.
    omit={'base_units':0,'omission_units':len(applicable(group))*settings.omission_cost_units,
          'missing_expected_fields':len(applicable(group)),'components':{},'soft_magnitudes':{'P01':0,'P02':0}}
    options.append((None,omit,[],('~OMIT',)))
    return options


def decode(graph,settings,mode,clock=time.monotonic):
    if mode not in {'constrained','unconstrained'}:
        raise ValueError('Explicit constrained/unconstrained mode required')
    if digest(settings.model_dump(mode='json')) != graph.settings_digest:
        raise ValueError('Candidate graph and decoder settings differ')
    started=clock()
    selected_all=[]; unselected=[]; ledgers=[]; findings_all=[]
    base_ledger=[]
    try:
        for groups in components(graph):
            budget=Budget(settings,clock,started)
            best=None; best_value=None; best_tie=None; rejected=Counter(); trace=[]
            try:
                if len(groups)>settings.max_component_groups:
                    raise SearchIncomplete('COMPONENT_GROUP_CAP')
                options=[_options(g,graph,settings,budget) for g in groups]
                component_bases=[]
                for group, domain in zip(groups,options):
                    component_bases.extend({'group_id':group.group_id,'node_tuple':list(tie),'score':record}
                                           for selection,record,_,tie in domain)
                for choices in product(*options):
                    assignments=[(g,choice[0]) for g,choice in zip(groups,choices) if choice[0] is not None]
                    findings=[f for choice in choices for f in choice[2]]+timetable_findings(assignments,graph)
                    budget.charge(assignments=1,operations=len(findings)+sum(len(s) for _,s in assignments))
                    valid=feasible(findings,mode)
                    value=sum(objective(choice[1],settings,mode) for choice in choices)
                    tie=tuple(node for choice in choices for node in choice[3])
                    if not valid:
                        rejected.update(f.rule_id for f in findings if f.passed is not True and (f.rule_id in SHARED or
                                        mode=='constrained' and f.rule_id in {'T01','R01','C01'} and f.passed is False or
                                        mode=='constrained' and f.rule_id=='R01' and f.passed is None))
                    if len(trace)<settings.trace_limit:
                        trace.append({'node_tuple':list(tie),'feasible':valid,'objective_units':value})
                    if valid and (best_value is None or value>best_value or value==best_value and tie<best_tie):
                        best,best_value,best_tie=(choices,findings),value,tie
                base_ledger.extend(component_bases)
                if best is None:
                    raise SearchIncomplete('NO_FEASIBLE_ASSIGNMENT')
                choices,findings=best
                findings_all.extend(findings)
                for group,choice in zip(groups,choices):
                    if choice[0] is None:
                        unselected.append((group,'OMITTED_NO_DEFENSIBLE_SELECTION'))
                    else:
                        selected_all.append((group,choice[0]))
                status='OPTIMAL_IN_RETAINED_DOMAIN'
                reason=None
            except SearchIncomplete as exc:
                status='SEARCH_INCOMPLETE'; reason=str(exc)
                unselected.extend((g,status) for g in groups)
                # Incumbent is diagnostics only; never emitted as a prediction.
            ledgers.append({'group_ids':[g.group_id for g in groups],'status':status,'reason':reason,
                            'assignments_visited':budget.assignments,'operations':budget.operations,
                            'objective_units':best_value if status!='SEARCH_INCOMPLETE' else None,
                            'incumbent_objective_diagnostic':best_value if status=='SEARCH_INCOMPLETE' else None,
                            'rejection_counts':dict(sorted(rejected.items())), 'search_trace':trace})
    except ExecutionAborted as exc:
        return {'status':'EXECUTION_ABORTED','reason':str(exc),'canonical_output':None,
                'execution_metadata':{'elapsed_seconds':clock()-started},'scorable':False}
    # Global diagnostics catch unknown relationships between independent components.
    findings_all.extend(timetable_findings(selected_all,graph))
    unique={digest(f.model_dump(mode='json')):f for f in findings_all}
    findings=tuple(unique[k] for k in sorted(unique))
    events=tuple(canonical_event(g,s,graph,mode,findings) for g,s in sorted(selected_all,key=lambda x:x[0].group_id))
    skeletons=tuple(canonical_event(g,None,graph,mode,unresolved=True) for g,_ in sorted(unselected,key=lambda x:x[0].group_id))
    packet=CanonicalTimetable(source=graph.source,evidence=graph.evidence,events=events,
        issues=graph.input_issues+tuple(ParseIssue(code=reason,evidence_ids=g.evidence_ids) for g,reason in unselected))
    canonical={'schema_version':'m4.0','profile_id':settings.profile_id,'research_status':settings.status,
               'decoder_mode':mode,'method_id':'P1-m4-v1' if mode=='constrained' else 'B3-matched-m4-v1',
               'status':'SEARCH_INCOMPLETE' if any(x['status']=='SEARCH_INCOMPLETE' for x in ledgers) else 'COMPLETE_FINITE_SEARCH',
               'packet':packet.model_dump(mode='json'),'unresolved_candidates':[e.model_dump(mode='json') for e in skeletons],
               'graph':graph.model_dump(mode='json'),'findings':[f.model_dump(mode='json') for f in findings],
               'components':ledgers,'base_score_digest':digest(base_ledger),
               'candidate_graph_digest':digest(graph.model_dump(mode='json')),
               'registered_blockers':list(settings.registered_blockers),'complete_schedule_verified':False,
               'scorable':False} # Development hypotheses are never formal evaluation output.
    return {'canonical_output':canonical,'canonical_digest':digest(canonical),
            'execution_metadata':{'elapsed_seconds':clock()-started,'capture_aliases':graph.capture_aliases,
                                  'input_revision_provenance':list(graph.input_revision_provenance)}}
