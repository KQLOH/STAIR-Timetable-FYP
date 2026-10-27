"""Explicit M4 development entry points; registered profile blocked until PRE-02."""
from backend.reconstruction.candidates import build_graph
from backend.reconstruction.decoder import decode
from backend.reconstruction.models import Context, development_settings


def reconstruct(packet,decoder_mode,context=Context(),settings=None):
    settings=settings or development_settings()
    return decode(build_graph(packet,settings,context),settings,decoder_mode)


def compare_modes(packet,context=Context(),settings=None):
    settings=settings or development_settings()
    graph=build_graph(packet,settings,context)
    runs={mode:decode(graph,settings,mode) for mode in ('constrained','unconstrained')}
    outputs=[r.get('canonical_output') for r in runs.values()]
    complete=all(o and o['status']=='COMPLETE_FINITE_SEARCH' for o in outputs)
    matched=complete and outputs[0]['base_score_digest']==outputs[1]['base_score_digest'] and all(
        a['assignments_visited']==b['assignments_visited'] and a['operations']==b['operations']
        for a,b in zip(outputs[0]['components'],outputs[1]['components']))
    return {'runs':runs,'matching_checks_passed':bool(matched),
            'comparison_scope':'Synthetic/software comparability only; development profile is not PRE-02 registered',
            'registered_comparator_ready':False}
