"""Opt-in encrypted local review snapshots; grants and bearer tokens never persist."""
from contextlib import contextmanager
from dataclasses import asdict
from functools import wraps
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from backend.models.evidence import EvidenceSpan
from backend.models.event import CanonicalTimetable, CanonicalEvent
from backend.reconstruction.models import Context, canonical_bytes, digest
from backend.review.binding import BoundRun
from backend.review.models import AuditEntry, CorrectionRecord, ReviewError, ReviewTask
from backend.review.store import ReviewStore, _Run, _Task
from backend.review.windows_security import WindowsProtection
from backend.review.secure_directory import initialize_private_directory, validate_storage_paths, is_reparse, writer_lock

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
STORAGE = 'LOCAL_WINDOWS_DPAPI_RESTART_PERSISTENT'


class ReviewJournal:
    def __init__(self, directory, owner, *, protection=None):
        requested = Path(directory)
        repository = Path(__file__).resolve().parents[2]
        if requested.is_absolute() and requested.is_relative_to(repository):
            raise ReviewError('REVIEW_STORAGE_MUST_BE_PRIVATE', 422)
        self.owner = owner
        self.protection = protection or WindowsProtection()
        self.directory = initialize_private_directory(requested)
        self.records = self.directory / 'records'

    @contextmanager
    def locked(self):
        try:
            validate_storage_paths(self.directory)
            with writer_lock(self.directory):
                yield
        except OSError:
            raise ReviewError('REVIEW_STORAGE_ACCESS_DENIED', 503) from None

    def load(self):
        previous, state, count = None, None, 0
        try:
            files = sorted(self.records.iterdir())
            if len(files) > 10000:
                raise ValueError('record limit')
            for count, path in enumerate(files, 1):
                if is_reparse(path) or not path.is_file() or path.stat().st_size > MAX_SNAPSHOT_BYTES:
                    raise ValueError('record bounds')
                raw = path.read_bytes(); fingerprint = sha256(raw).hexdigest()
                if path.name != f'{count:08d}-{fingerprint}.dpapi':
                    raise ValueError('record sequence')
                record = json.loads(self.protection.unprotect(raw))
                if (record['version'] != 1 or record['owner'] != self.owner or
                        record['sequence'] != count or record['previous'] != previous):
                    raise ValueError('record chain or owner')
                state, previous = record['state'], fingerprint
            anchor = self.directory / 'head.dpapi'
            if is_reparse(anchor):
                raise ValueError('head path')
            if files or anchor.exists():
                if anchor.stat().st_size > 4096:
                    raise ValueError('head size')
                head = json.loads(self.protection.unprotect(anchor.read_bytes()))
                if head != {'owner': self.owner, 'sequence': count, 'hash': previous}:
                    raise ValueError('missing or uncommitted tail')
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            raise ReviewError('REVIEW_STORAGE_CORRUPT_OR_WRONG_USER', 409) from None
        return state, count, previous

    def append(self, state):
        _, count, previous = self.load()
        if count >= 10000:
            raise ReviewError('REVIEW_STORAGE_CAPACITY', 413)
        raw = canonical_bytes({'version': 1, 'owner': self.owner, 'sequence': count + 1,
                               'previous': previous, 'state': state})
        if len(raw) > MAX_SNAPSHOT_BYTES - 4096:
            raise ReviewError('REVIEW_STORAGE_CAPACITY', 413)
        encrypted = self.protection.protect(raw)
        stage = self.directory / (uuid4().hex + '.staging')
        with stage.open('xb') as handle:
            handle.write(encrypted); handle.flush(); os.fsync(handle.fileno())
        os.link(stage, self.records / f'{count + 1:08d}-{sha256(encrypted).hexdigest()}.dpapi')
        anchor = self.directory / 'head.dpapi'
        if is_reparse(anchor):
            raise ReviewError('REVIEW_STORAGE_PATH_INVALID', 422)
        head = self.protection.protect(canonical_bytes({'owner': self.owner, 'sequence': count + 1,
                                                       'hash': sha256(encrypted).hexdigest()}))
        pending_head = self.directory / (uuid4().hex + '.head-staging')
        with pending_head.open('xb') as handle:
            handle.write(head); handle.flush(); os.fsync(handle.fileno())
        os.replace(pending_head, anchor)
        # Unpublished staging files are retained, never interpreted as commits.


def _persisted(method, write=False):
    @wraps(method)
    def transaction(self, *args, **kwargs):
        with self._lock:
            if self._transaction_active:
                return method(self, *args, **kwargs)
            with self.journal.locked():
                state, _, _ = self.journal.load()
                self._restore(state)
                before = self._dump()
                self._transaction_active = True
                try:
                    value = method(self, *args, **kwargs)
                    if write and (write != 'changed' or self._dump() != before):
                        self.journal.append(self._dump())
                    return value
                except (OSError, RuntimeError):
                    self._restore(before)
                    raise ReviewError('REVIEW_STORAGE_WRITE_FAILED', 503) from None
                except Exception:
                    self._restore(before)
                    raise
                finally:
                    self._transaction_active = False
    return transaction


class PersistentReviewStore(ReviewStore):
    storage_kind = STORAGE

    def __init__(self, authority, journal, **kwargs):
        super().__init__(authority, **kwargs)
        self.journal = journal
        self._transaction_active = False
        with journal.locked():
            state, _, _ = journal.load(); self._restore(state)

    def _dump(self):
        return {'runs': {key: {'snapshot': asdict(run.snapshot),
                               'evidence': [s.model_dump(mode='json') for s in run.evidence],
                               'context': run.context.model_dump(mode='json') if run.context else None,
                               'events': {k: v.model_dump(mode='json') for k, v in run.events.items()},
                               'requests': sorted(run.requests)} for key, run in self._runs.items()},
                'tasks': {key: {'view': task.view.model_dump(mode='json'),
                                'audits': [a.model_dump(mode='json') for a in task.audits],
                                'corrections': [c.model_dump(mode='json') for c in task.corrections],
                                'revisions': task.revisions, 'decisions': task.decisions,
                                'assessments': task.assessments} for key, task in self._tasks.items()}}

    def _restore(self, state):
        if state is None:
            self._runs, self._tasks = {}, {}; return
        runs, tasks = {}, {}
        try:
            for key, raw in state['runs'].items():
                bound = dict(raw['snapshot'])
                for name in ('candidate_groups', 'component_statuses'):
                    bound[name] = tuple(tuple(pair) for pair in bound[name])
                for name in ('events_json', 'unresolved_ids'):
                    bound[name] = tuple(bound[name])
                snapshot = BoundRun(**bound)
                if key != digest({'input': snapshot.input_digest, 'reconstruction': snapshot.reconstruction_digest}):
                    raise ValueError('run binding')
                evidence = tuple(EvidenceSpan.model_validate(s) for s in raw['evidence'])
                events = {k: CanonicalEvent.model_validate(v) for k, v in raw['events'].items()}
                source = CanonicalTimetable.model_validate_json(snapshot.source_json).source
                CanonicalTimetable(source=source, evidence=evidence, events=tuple(events.values()))
                runs[key] = _Run(snapshot, evidence, Context.model_validate(raw['context']) if raw['context'] else None,
                                 events, set(raw['requests']))
            for key, raw in state['tasks'].items():
                view = ReviewTask.model_validate(raw['view'])
                if key != str(view.task_id) or view.run_id not in runs:
                    raise ValueError('task binding')
                audits = tuple(AuditEntry.model_validate(a) for a in raw['audits'])
                previous = None
                for number, audit in enumerate(audits, 1):
                    if (audit.sequence != number or audit.previous_hash != previous or
                            audit.entry_hash != digest(audit.model_dump(mode='json', exclude={'entry_hash'}))):
                        raise ValueError('audit chain')
                    previous = audit.entry_hash
                revisions = tuple(raw['revisions'])
                prior = None
                for text in revisions:
                    revision = CanonicalEvent.model_validate_json(text).revision
                    if prior and (revision.previous_revision_id != prior.revision_id or revision.version != prior.version + 1):
                        raise ValueError('revision chain')
                    prior = revision
                if revisions and digest(CanonicalEvent.model_validate_json(revisions[0]).model_dump(mode='json')) != view.original_prediction_digest:
                    raise ValueError('original prediction')
                if view.current_event and view.current_event != runs[view.run_id].events[view.current_event.candidate_id]:
                    raise ValueError('current event')
                tasks[key] = _Task(view, audits, tuple(CorrectionRecord.model_validate(c) for c in raw['corrections']),
                                   revisions, tuple(raw['decisions']), tuple(raw['assessments']))
        except (ValueError, KeyError, TypeError):
            raise ReviewError('REVIEW_STORAGE_INVALID_STATE', 409) from None
        self._runs, self._tasks = runs, tasks

    connect_run = _persisted(ReviewStore.connect_run, 'changed')
    ingest = _persisted(ReviewStore.ingest, True)
    assign = _persisted(ReviewStore.assign, True)
    start = _persisted(ReviewStore.start, True)
    resume = _persisted(ReviewStore.resume, True)
    submit = _persisted(ReviewStore.submit, True)
    revalidate = _persisted(ReviewStore.revalidate, True)
    finish = _persisted(ReviewStore.finish, True)
    get = _persisted(ReviewStore.get)
    queue_summary = _persisted(ReviewStore.queue_summary)
    history = _persisted(ReviewStore.history)
    evidence = _persisted(ReviewStore.evidence)
    original_reconstruction = _persisted(ReviewStore.original_reconstruction)
    source = _persisted(ReviewStore.source)
    snapshot = _persisted(ReviewStore.snapshot)
