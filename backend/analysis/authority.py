"""Host-installed independent verifier evidence. No public minting or auto-audit.

Integrity checks cannot prove source correctness: an authorized independent human
must supply the actual source-first inventory and recurrence adjudication record.
Application startup installs no verifier, inventory or complete attestation.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from secrets import token_urlsafe
from backend.reconstruction.models import digest
from backend.analysis.models import binding
from backend.calendar.mapping import normalize_occurrences


@dataclass(frozen=True)
class Verifier:
    actor_id: str
    expires_at: datetime


class CompletenessAuthority:
    def __init__(self, clock=lambda: datetime.now(timezone.utc)):
        self.clock = clock
        self._verifiers = {}
        self._records = {}

    def install_verifier(self, actor_id, *, independent_of, expires_at):
        # Trusted bootstrap only. M5 reviewer/owner/TAR principals cannot substitute.
        if not actor_id or not independent_of or actor_id in independent_of or expires_at.tzinfo is None or expires_at <= self.clock():
            raise ValueError('Independent verifier identity and bounded authority required')
        grant = Verifier(actor_id, expires_at)
        self._verifiers[id(grant)] = grant
        return grant

    def record(self, request, inventory, verifier):
        self._require(verifier)
        source = request.packet.source
        expected = {(e.candidate_id, str(e.revision.revision_id)) for e in request.packet.events}
        mapped = [(r['candidate_id'], r['revision_id']) for r in inventory['sessions']]
        # Inventory assertions bind the exact finite occurrence/exception result,
        # rather than treating equal event counts as recurrence reconciliation.
        for event in request.packet.events:
            row = next((r for r in inventory['sessions'] if r['candidate_id'] == event.candidate_id), None)
            occurrences, excluded = normalize_occurrences(request, event)
            expected_occurrences = digest({'occurrences': [o.model_dump(mode='json') for o in occurrences],
                                           'excluded_dates': sorted(d.isoformat() for d in excluded)})
            if row is None or not row.get('source_reconciliation_record_id') or row.get('occurrence_digest') != expected_occurrences:
                raise ValueError('Independent recurrence reconciliation required')
        pages = inventory['required_pages']; regions = inventory['required_regions']
        if (request.declared_kind == 'EXPLICITLY_INCOMPLETE' or not source.timetable_id or
            request.scope.timetable_id != source.timetable_id or
            inventory['source_document_id'] != source.source_document_id or inventory['source_sha256'] != source.content_sha256 or
            not pages or len(set(pages)) != len(pages) or set(pages) != set(inventory['checked_pages']) or
            not regions or len(set(regions)) != len(regions) or set(regions) != set(inventory['checked_regions']) or
            not inventory['provenance_record_id'] or not inventory['recurrence_adjudication_record_id'] or not inventory['field_reconciliation_record_id'] or
            inventory['unresolved'] or inventory['excluded_scope'] or
            len(mapped) != len(set(mapped)) or set(mapped) != expected or
            len({r['source_session_id'] for r in inventory['sessions']}) != len(mapped) or
            any(r['page'] not in pages or r['region_id'] not in regions or not r['source_session_id'] for r in inventory['sessions'])):
            raise ValueError('Source-first inventory coverage/bijection unresolved')
        token = token_urlsafe(32)
        self._records[token] = (binding(request), digest(inventory), verifier, inventory['provenance_record_id'])
        return token

    def _require(self, verifier):
        if self._verifiers.get(id(verifier)) is not verifier or verifier.expires_at <= self.clock():
            raise ValueError('Independent verifier authority missing or expired')

    def revoke(self, attestation_id):
        self._records.pop(attestation_id, None)

    def assess(self, request):
        record = self._records.get(request.attestation_id)
        if record is None: return False, 'TRUSTED_COMPLETENESS_RECORD_REQUIRED'
        try: self._require(record[2])
        except ValueError: return False, 'VERIFIER_AUTHORITY_REVOKED_OR_EXPIRED'
        if binding(request) != record[0]: return False, 'STALE_SOURCE_SCOPE_OR_REVISION'
        return True, 'SOURCE_INVENTORY_AND_REVISIONS_BOUND'

    def proof(self, request):
        if not self.assess(request)[0]: return None
        record=self._records[request.attestation_id]
        return {'inventory_digest':record[1], 'source_reconciliation_record_id':record[3],
                'authority_kind':'TRUSTED_HOST_INDEPENDENT_VERIFIER', 'synthetic':request.declared_kind=='SYNTHETIC_DEMO'}
