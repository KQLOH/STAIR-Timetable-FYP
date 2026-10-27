"""Explicit synthetic-only authority factory. Never installed in application startup/API."""
import json
from hashlib import sha256
from uuid import uuid4

from backend.models.evidence import SourceDocument,EvidenceSpan
from backend.models.event import (CanonicalTimetable,CanonicalEvent,EventContent,EventIdentity,EventRevision,FieldObservation,
    FieldState,ExtractionStatus,ConstraintValidation,RuleTrace,stable_session_key)
from backend.calendar.models import CalendarInput,CalendarContext,WeekAuthority,Lineage,CalendarError
from backend.calendar.mapping import map_event
from backend.reconstruction.models import TermAuthority,digest


class SyntheticFixtureAuthority:
    def __init__(self,sandbox):
        if sandbox.snapshot_scope!='TEST_ONLY': raise ValueError('An isolated test sandbox is required')
        self._registered=set();sandbox.fixtures=self

    def authorizes(self,bundle):
        return digest(bundle.model_dump(mode='json')) in self._registered

    def make(self,*,events=None,previous=None,changes=None,capture_id=None,lineage=False,weeks=(),term=None):
        originals=previous.packet.events if previous else ()
        if events is None:
            events=[{**e.revision.content.model_dump(mode='json'),'session_id':e.identity.stable_source_session_id} for e in originals] if originals else [
                {'course_code':'SYN101','session_type':'Lecture','day_of_week':1,'date':'2026-01-05','start_time':'09:00',
                 'end_time':'10:00','source_timezone':'Etc/UTC','location':'SYNTHETIC TEST ROOM','session_id':'synthetic-session:1'}]
        if changes: events=[{**events[0],**changes},*events[1:]]
        raw='SYNTHETIC M6 TEST DATA ONLY\n'+json.dumps(events,sort_keys=True)
        docid=capture_id or (previous.packet.source.source_document_id if previous else 'synthetic:capture:'+uuid4().hex)
        if not docid.startswith('synthetic:'): raise ValueError('Synthetic capture IDs required')
        source=previous.packet.source if previous and capture_id is None else SourceDocument(source_document_id=docid,
            content_sha256=sha256(raw.encode()).hexdigest(),route='text',media_type='text/plain',source_family_id='synthetic-family',
            timetable_id='synthetic-table',predecessor_document_id=previous.packet.source.source_document_id if previous and lineage else None,
            content_hash_basis='decoded_text_utf8')
        sid='synthetic:evidence:'+uuid4().hex
        source_span=EvidenceSpan(evidence_id=sid,source_document_id=docid,route='text',raw_text=raw,page=1,
                                 transformations=('synthetic-m6-fixture-v1',))
        if previous and capture_id is None:
            spans=list(previous.packet.evidence)+[source_span]
        else: spans=[source_span]
        authority_id='synthetic:authority:'+uuid4().hex
        authority_text='SYNTHETIC TEST AUTHORITY '+json.dumps({'events':events,'term':term,'weeks':weeks,
            'predecessor':previous.packet.source.source_document_id if previous else None,'successor':docid},sort_keys=True)
        spans.append(EvidenceSpan(evidence_id=authority_id,source_document_id=docid,route='authority',raw_text=authority_text,page=1,
                                  transformations=('synthetic-m6-fixture-v1',)))
        context=CalendarContext(timezone_evidence_ids=(authority_id,),exception_evidence_ids=(authority_id,),
            term=TermAuthority(start=term[0],end=term[1],authority_record_id='synthetic-term',evidence_ids=(authority_id,)) if term else None,
            weeks=tuple(WeekAuthority(week=w[0],start=w[1],end=w[2],evidence_ids=(authority_id,)) for w in weeks),
            lineage=(Lineage(predecessor_document_id=previous.packet.source.source_document_id,successor_document_id=docid,evidence_ids=(authority_id,)),) if previous and lineage else ())
        candidates=[]
        for i,item in enumerate(events):
            item=dict(item);session=item.pop('session_id','synthetic-session:'+str(i))
            if not session.startswith('synthetic-session:'): raise ValueError('Synthetic session IDs required')
            old=next((e for e in originals if e.identity.stable_source_session_id==session),None)
            unknown={k:v for k,v in item.items() if v in ('TBA','UNKNOWN')}
            content=EventContent.model_validate({k:None if k in unknown else v for k,v in item.items()})
            observations=tuple(FieldObservation(field_name=f,state='UNRESOLVED' if f in unknown else 'PRESENT' if getattr(content,f) is not None else 'MISSING',
                raw_value=unknown[f] if f in unknown else json.dumps(content.model_dump(mode='json')[f]) if getattr(content,f) is not None else None,
                evidence_ids=(sid,) if getattr(content,f) is not None or f in unknown else (),origin='human' if previous else 'source',
                transformations=('synthetic-m6-fixture-v1',)) for f in EventContent.model_fields)
            identity=EventIdentity(event_id=old.identity.event_id if old else uuid4(),status='CONFIRMED',
                source_family_id=source.source_family_id,timetable_id=source.timetable_id,stable_source_session_id=session,
                stable_key=stable_session_key(source.source_family_id,source.timetable_id,session),evidence_ids=(sid,))
            revision=EventRevision(revision_id=uuid4(),version=old.revision.version+1 if old else 1,
                previous_revision_id=old.revision.revision_id if old else None,source_document_id=docid,
                method_id='synthetic-m6-fixture-v1',configuration_digest=digest({'fixture':'synthetic-m6-fixture-v1'}),content=content,observations=observations)
            candidates.append(CanonicalEvent(candidate_id=old.candidate_id if old else 'synthetic:candidate:'+uuid4().hex,
                identity=identity,revision=revision,extraction_status='EXTRACTED' if revision.core_fields_present else 'PARTIAL'))
        packet=CanonicalTimetable(source=source,evidence=tuple(spans),events=tuple(candidates))
        bundle=CalendarInput(packet=packet,reconstruction_status='COMPLETE_FINITE_SEARCH',context=context)
        validated=[]
        for event in candidates:
            try: map_event(bundle,event);valid=True
            except CalendarError: valid=False
            validation=ConstraintValidation(status='VALID' if valid else 'FAILED',revision_id=event.revision.revision_id,
                validator_version='synthetic-m6-mapping-check-v1',traces=(RuleTrace(code='SYNTHETIC_MAPPING_CHECK',passed=valid,severity='HARD'),))
            validated.append(event.model_copy(update={'validation':validation}))
        bundle=bundle.model_copy(update={'packet':packet.model_copy(update={'events':tuple(validated)})})
        self._registered.add(digest(bundle.model_dump(mode='json')))
        return bundle

    def empty_observation(self,previous):
        result=previous.model_copy(update={'packet':previous.packet.model_copy(update={'events':()}),'reconstruction_status':'SEARCH_INCOMPLETE'})
        self._registered.add(digest(result.model_dump(mode='json')))
        return result
