"""Bind application layout repair to exact original code, never a new research method."""
import json
from backend.models.event import CanonicalEvent
from backend.reconstruction.models import Context, development_settings, digest
from backend.review.binding import BoundRun
from backend.review.models import ReviewError


def bind_layout_review(request,profile):
    if request.policy_profile!='m5-development-v1':
        raise ReviewError('REGISTERED_POLICY_UNAVAILABLE',409)
    if request.reconstruction_context!=Context() or request.reconstruction_settings!=development_settings():
        raise ReviewError('REVIEW_LAYOUT_CONTEXT_UNSUPPORTED',422)
    if request.source_packet.source.route!='ocr':
        raise ReviewError('OCR_LAYOUT_ROUTE_REQUIRED',422)
    try:
        if profile=='ocr-layout-m52-v1':
            if request.layout_evidence is not None:raise ValueError('Unexpected sidecar')
            from backend.layout.service import reconstruct_layout
            expected,graph,mapping=reconstruct_layout(request.source_packet,request.decoder_mode)
        elif profile=='ocr-ruled-grid-s2a-v1':
            from backend.layout.ruled import reconstruct_ruled
            from backend.layout.ruled_models import RuledEvidence
            sidecar=RuledEvidence.model_validate(request.layout_evidence)
            expected,graph,mapping=reconstruct_ruled(request.source_packet,sidecar,request.decoder_mode)
        else:
            from backend.layout.block_grid import reconstruct_blocks
            from backend.layout.block_models import BlockEvidence
            sidecar=BlockEvidence.model_validate(request.layout_evidence)
            expected,graph,mapping=reconstruct_blocks(request.source_packet,sidecar,request.decoder_mode)
        canonical=expected['canonical_output']
        if canonical is None or request.reconstruction.get('canonical_digest')!=expected['canonical_digest']:
            raise ValueError('Aborted or changed result')
        if digest(request.reconstruction.get('canonical_output'))!=digest(canonical):
            raise ValueError('Canonical result mismatch')
        events=tuple(CanonicalEvent.model_validate(e) for e in canonical['packet']['events']+canonical['unresolved_candidates'])
        return BoundRun(json.dumps(request.reconstruction,allow_nan=False),
            digest({'packet':request.source_packet.model_dump(mode='json'),'graph':graph.model_dump(mode='json'),'mode':request.decoder_mode}),
            expected['canonical_digest'],request.source_packet.model_dump_json(),graph.model_dump_json(),request.decoder_mode,
            profile,canonical['status'],canonical['base_score_digest'],tuple(mapping.items()),
            tuple(e.model_dump_json() for e in events),tuple(e['candidate_id'] for e in canonical['unresolved_candidates']),
            tuple((gid,c['status']) for c in canonical['components'] for gid in c['group_ids']),json.dumps(canonical['findings']))
    except (ValueError,KeyError,TypeError):
        raise ReviewError('INVALID_LAYOUT_REVIEW_BINDING',422) from None
