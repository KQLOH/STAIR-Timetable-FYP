"""Deterministic local diff plans. Missing observations never become deletions."""
from uuid import uuid5,NAMESPACE_URL
from backend.calendar.models import Preview,Operation,SourceBinding
from backend.calendar.eligibility import assess
from backend.calendar.mapping import _supported
from backend.reconstruction.models import digest
from backend.review.auth import utc_now


def binding(bundle,event):
    return SourceBinding(stable_key=event.identity.stable_key,candidate_id=event.candidate_id,
        revision_id=event.revision.revision_id,revision_digest=digest(event.revision.model_dump(mode='json')),
        source_document_id=bundle.packet.source.source_document_id,bundle_digest=digest(bundle.model_dump(mode='json')))


def build_preview(bundle,snapshot,*,test_authorized=False,clock=utc_now):
    results=tuple(assess(bundle,e,test_authorized=test_authorized) for e in bundle.packet.events)
    keys=[e.identity.stable_key for e in bundle.packet.events if e.identity.stable_key]
    operations=[]; observed=set(keys)
    def operation(action,candidate,key,entry,before,after,reasons):
        left=before.model_dump(mode='json') if before else None
        right=after.model_dump(mode='json') if after else None
        changed=tuple(sorted(k for k in set(left or {})|set(right or {}) if digest((left or {}).get(k))!=digest((right or {}).get(k))))
        opkey=digest({'target':snapshot.sandbox_id,'action':action,'identity':key,'candidate':candidate,'before':left,'after':right})
        return Operation(operation_key=opkey,action=action,candidate_id=candidate,stable_key=key,entry_id=entry,
                         before=before,after=after,changed_fields=changed,reason_codes=tuple(sorted(reasons)))
    for event,result in zip(bundle.packet.events,results):
        key=event.identity.stable_key
        matches=[e for e in snapshot.entries if key and e.payload.stable_key==key]
        before=matches[0].payload if len(matches)==1 else None
        if key and keys.count(key)>1:
            action,reasons='CONFLICT',('DUPLICATE_INPUT_IDENTITY',)
        elif len(matches)>1:
            action,reasons='CONFLICT',('MULTIPLE_UPDATE_TARGETS',)
        elif matches and not matches[0].managed:
            action,reasons='CONFLICT',('UNMANAGED_TARGET',)
        elif not result.sandbox_write_permitted:
            action,reasons='BLOCKED',result.reason_codes
        elif before and before.source_document_id!=bundle.packet.source.source_document_id and not any(
            line.predecessor_document_id==before.source_document_id and line.successor_document_id==bundle.packet.source.source_document_id
            and bundle.packet.source.predecessor_document_id==before.source_document_id
            and _supported(line.evidence_ids,bundle,(before.source_document_id,bundle.packet.source.source_document_id)) for line in bundle.context.lineage):
            action,reasons='CONFLICT',('AMBIGUOUS_PREDECESSOR',)
        elif before and before.source_document_id==bundle.packet.source.source_document_id and before.source_content_sha256!=bundle.packet.source.content_sha256:
            action,reasons='CONFLICT',('SOURCE_CAPTURE_HASH_CONFLICT',)
        else:
            action='CREATE' if not before else 'NO_OP' if digest(before.model_dump(mode='json'))==digest(result.payload.model_dump(mode='json')) else 'UPDATE'
            reasons=()
        entry=matches[0].entry_id if len(matches)==1 else 'local-'+digest({'target':snapshot.sandbox_id,'stable_key':key}) if key else None
        operations.append(operation(action,event.candidate_id,key,entry,before,result.payload,reasons))
    # Only demonstrated same-cohort/occurrence conflicts block the sandbox plan.
    proposed=tuple(operations)
    for i,op in enumerate(proposed):
        if op.action not in {'CREATE','UPDATE'} or not op.after: continue
        others=[e.payload for e in snapshot.entries if e.payload.stable_key!=op.stable_key]+[
            other.after for other in proposed if other.stable_key!=op.stable_key and other.after and other.action in {'CREATE','UPDATE'}]
        conflict=any(op.after.cohort_id and op.after.cohort_id==other.cohort_id and
                     not (op.after.section and other.section and op.after.section!=other.section) and
                     any(max(a.start,b.start)<min(a.end,b.end) for a in op.after.occurrences for b in other.occurrences) for other in others)
        if conflict:
            operations[i]=operation('CONFLICT',op.candidate_id,op.stable_key,op.entry_id,op.before,op.after,('EVIDENCED_COHORT_OVERLAP',))
    for entry in snapshot.entries:
        if entry.managed and entry.payload.stable_key not in observed:
            operations.append(operation('MISSING_OBSERVATION',None,entry.payload.stable_key,entry.entry_id,entry.payload,None,('REMOVAL_REVIEW_REQUIRED','NO_AUTOMATIC_DELETE')))
    preview=Preview(target_sandbox_id=snapshot.sandbox_id,scope=snapshot.scope,snapshot_version=snapshot.calendar_version,
        snapshot_digest=snapshot.checksum(),input_digest=digest(bundle.model_dump(mode='json')),
        source_bindings=tuple(sorted((binding(bundle,e) for e in bundle.packet.events if e.identity.stable_key),key=lambda b:b.stable_key)),
        operations=tuple(sorted(operations,key=lambda op:op.operation_key)),eligibility=results,
        generated_at=clock(),preview_digest='0'*64)
    return preview.model_copy(update={'preview_digest':preview.checksum()})
