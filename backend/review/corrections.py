"""Evidence-linked human revisions. Source extraction and predictions remain immutable."""
import json
import re
from uuid import uuid4
from pydantic import ValidationError

from backend.models.evidence import EvidenceSpan
from backend.models.event import (CanonicalTimetable, CanonicalEvent, EventContent, EventRevision, FieldObservation,
                                  FieldState, HumanReview, ExtractionStatus)
from backend.reconstruction.candidates import normalize
from backend.reconstruction.models import digest
from backend.review.models import ReviewError
from backend.review.validation import validate_context


def _text(value):
    return re.sub(r'\s+',' ',value).strip().casefold()


def prepare_correction(event, submission, evidence, source, context, actor, timestamp):
    evidence=list(evidence)
    known={e.evidence_id:e for e in evidence}
    for addition in submission.human_evidence:
        if addition.evidence_id in known or any(i not in known or i.startswith('human:') for i in addition.source_evidence_ids):
            raise ReviewError('INVALID_HUMAN_EVIDENCE',422)
        pages={known[i].page for i in addition.source_evidence_ids}
        if len(pages)>1:
            raise ReviewError('HUMAN_EVIDENCE_PAGE_MISMATCH',422)
        if addition.page is not None and addition.page not in pages:
            raise ReviewError('HUMAN_EVIDENCE_PAGE_MISMATCH',422)
        span=EvidenceSpan(evidence_id=addition.evidence_id,source_document_id=source.source_document_id,route='text',
            raw_text=addition.raw_text,page=addition.page,bbox=addition.bbox,
            transformations=('m5-human-assertion-not-original-extraction','actor:'+actor,
                             'asserted-at:'+timestamp.isoformat(),*('annotates:'+i for i in addition.source_evidence_ids)))
        evidence.append(span); known[span.evidence_id]=span
    content=event.revision.content.model_dump(mode='json')
    observations={o.field_name:o for o in event.revision.observations}
    for change in submission.fields:
        if any(ref not in known for ref in change.evidence_ids):
            raise ReviewError('BROKEN_CORRECTION_EVIDENCE',422)
        if len({known[ref].page for ref in change.evidence_ids})>1:
            raise ReviewError('CORRECTION_CROSS_PAGE_EVIDENCE',422)
        for alternative in change.alternatives:
            if any(ref not in known for ref in alternative.evidence_ids) or not any(_text(alternative.raw_value) in _text(known[i].raw_text) for i in alternative.evidence_ids):
                raise ReviewError('UNSUPPORTED_CORRECTION_ALTERNATIVE',422)
            if len({known[ref].page for ref in (*change.evidence_ids,*alternative.evidence_ids)})>1:
                raise ReviewError('CORRECTION_CROSS_PAGE_EVIDENCE',422)
        if change.raw_value and not any(_text(change.raw_value) in _text(known[i].raw_text) for i in change.evidence_ids):
            raise ReviewError('UNSUPPORTED_CORRECTION_VALUE',422)
        if change.state==FieldState.PRESENT:
            if not change.raw_value or not any(_text(change.raw_value) in _text(known[i].raw_text) for i in change.evidence_ids):
                raise ReviewError('UNSUPPORTED_CORRECTION_VALUE',422)
            normalized=normalize(change.field_name,change.raw_value)
            if change.field_name=='recurrence' and change.raw_value.lstrip().startswith('{'):
                try: normalized=json.loads(change.raw_value)
                except ValueError: normalized=None
            if normalized is None or digest(normalized)!=digest(change.value):
                raise ReviewError('CORRECTION_NORMALIZATION_MISMATCH',422)
            content[change.field_name]=change.value
        else:
            if change.value is not None:
                raise ReviewError('FIELD_STATE_VALUE_MISMATCH',422)
            content[change.field_name]=None
        observations[change.field_name]=FieldObservation(field_name=change.field_name,state=change.state,raw_value=change.raw_value,
            origin='human',evidence_ids=change.evidence_ids,alternatives=change.alternatives,confidence=None,
            justification=change.justification,transformations=('m5-human-correction-v1',))
    updated_context=submission.relationships or context
    validate_context(updated_context,evidence)
    if submission.relationships is not None and updated_context==context and not submission.fields:
        raise ReviewError('EMPTY_CORRECTION',422)
    if content==event.revision.content.model_dump(mode='json') and tuple(observations.values())==event.revision.observations and updated_context==context:
        raise ReviewError('EMPTY_CORRECTION',422)
    revision=EventRevision(revision_id=uuid4(),version=event.revision.version+1,previous_revision_id=event.revision.revision_id,
        source_document_id=event.revision.source_document_id,method_id='m5-human-correction-v1',
        configuration_digest=digest({'source_revision':str(event.revision.revision_id),'relationships':updated_context.model_dump(mode='json'),
                                     'adapter':'m5-human-correction-v1'}),content=EventContent.model_validate(content),
        observations=tuple(observations[o.field_name] for o in event.revision.observations))
    ambiguous=any(o.state in {FieldState.UNRESOLVED,FieldState.CONFLICTING} or len(o.alternatives)>1 for o in revision.observations)
    updated=CanonicalEvent(candidate_id=event.candidate_id,identity=event.identity,revision=revision,
        extraction_status=ExtractionStatus.AMBIGUOUS if ambiguous else (ExtractionStatus.EXTRACTED if revision.core_fields_present else ExtractionStatus.PARTIAL),
        human_review=HumanReview(status='CORRECTED',revision_id=revision.revision_id,actor_id=actor,recorded_at=timestamp,
                                evidence_ids=tuple(sorted({i for c in submission.fields for i in c.evidence_ids}))))
    # Validate every observation/alternative and its capture binding, including new annotations.
    CanonicalTimetable(source=source,evidence=tuple(evidence),events=(updated,))
    return updated,tuple(evidence),updated_context
