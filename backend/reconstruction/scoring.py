"""Fixed-point diagnostic scores, not calibrated probabilities or acceptance ranks."""
from decimal import Decimal, ROUND_HALF_EVEN
from backend.models.event import FieldState
from backend.reconstruction.models import Finding


def fixed(value):
    return int(Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_EVEN))


def applicable(group):
    # Core alternatives count once each; source-visible conditionals remain expected.
    fields = [field for field, nodes in group.domains.items() if any(n.state != FieldState.MISSING for n in nodes)
              and field not in {'course_code','course_name','day_of_week','date','start_time','end_time'}]
    return [('course_code','course_name'), ('day_of_week','date'), ('start_time',), ('end_time',)] + [(f,) for f in fields]


def support(node, graph, settings):
    if node.state != FieldState.PRESENT or node.confidence is None:
        return 0
    route = next(s.route for s in graph.evidence if s.evidence_id in node.evidence_ids)
    table = settings.route_support_tables.get(route, ())
    x = fixed(Decimal(str(node.confidence)) * 10000)
    if not table:
        return 0
    # Explicit monotone step lookup. No invented confidence for native evidence.
    return max((y for bound,y in table if bound <= x), default=0)


def score(group, selection, graph, settings):
    fields = applicable(group)
    present = lambda n: n.state == FieldState.PRESENT
    complete = sum(any(present(selection[f]) for f in alternatives) for alternatives in fields)
    extraction = sum(max((support(selection[f],graph,settings) for f in alternatives), default=0) for alternatives in fields)
    chosen = {n.node_id for n in selection.values() if present(n)}
    edges = [edge for edge in group.relations if edge.left in chosen and edge.right in chosen]
    components = {'extraction':fixed(Decimal(extraction)/len(fields)),
                  'relations':fixed(Decimal(sum(e.compatible for e in edges))*10000/len(edges)) if edges else 0,
                  'completeness':fixed(Decimal(complete)*10000/len(fields))}
    base = fixed(sum(Decimal(components[k])*w for k,w in settings.base_weights.items())/10000)
    missing = len(fields)-complete
    penalty = {}
    min_support = settings.provenance_min_support_units
    weak = sum(0 < support(n,graph,settings) < min_support for n in selection.values()) if min_support is not None else 0
    penalty['P01'] = fixed(Decimal(weak)*10000/len(fields))
    threshold = settings.typical_relation_min_units
    penalty['P02'] = max(0, threshold-components['relations']) if threshold is not None else 0
    return {'base_units':base, 'components':components, 'missing_expected_fields':missing,
            'omission_units':missing*settings.omission_cost_units, 'soft_magnitudes':penalty,
            'support_note':'Integer support index; unknown confidence contributes zero, never a manufactured probability'}


def objective(record, settings, mode):
    soft = fixed(sum(Decimal(record['soft_magnitudes'][k])*w for k,w in settings.soft_weights.items())/10000)
    return record['base_units']-record['omission_units']-(soft if mode == 'constrained' else 0)


def soft_findings(group, record, settings):
    criteria={'P01':settings.provenance_min_support_units,'P02':settings.typical_relation_min_units}
    return [Finding(rule_id=code,passed=None if criteria[code] is None else record['soft_magnitudes'][code]==0,
                    severity='SOFT',reason='Criterion not frozen' if criteria[code] is None else 'Configured provisional support/relationship criterion',
                    group_ids=(group.group_id,),evidence_ids=group.evidence_ids,magnitude_units=record['soft_magnitudes'][code])
            for code in ('P01','P02')]
