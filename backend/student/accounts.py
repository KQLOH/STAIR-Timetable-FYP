"""Password accounts and short-lived server sessions over the existing private journal.

No database migration. Passwords use salted scrypt; sessions never persist.
The launcher is deliberately single-process and loopback-only.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256, scrypt
from secrets import token_hex, token_urlsafe, compare_digest
from threading import RLock
import re
import time

from backend.review.auth import TrustedHostAuthority, PERMISSIONS
from backend.review.models import ReviewError
from backend.review.persistence import PersistentReviewStore

COOKIE = 'stair_student_session'
SESSION_SECONDS = 3600


@dataclass
class Session:
    user_id: str
    csrf: str
    expires: float
    principal: object = None
    portals: set = field(default_factory=set)
    google: dict = field(default_factory=dict)
    oauth: dict = field(default_factory=dict)


class Accounts:
    def __init__(self, journal, journal_factory, *, registry=None, clock=time.time,
                 calendar_policy='human-confirmed', calendar_context=None):
        if calendar_policy not in {'human-confirmed', 'registered-accept'}:
            raise ValueError('Unknown calendar policy')
        self.journal, self.journal_factory = journal, journal_factory
        self.registry, self.clock = registry, clock
        self.calendar_policy = calendar_policy
        self.calendar_context = calendar_context
        self.lock = RLock()
        self.sessions, self.stores, self.failures = {}, {}, {}
        self._dummy_salt = token_hex(16)

    @contextmanager
    def transaction(self, write=False):
        with self.lock, self.journal.locked():
            state, _, _ = self.journal.load()
            state = state or {'accounts': {}, 'workspaces': {}}
            yield state
            if write:
                self.journal.append(state)

    @staticmethod
    def password_hash(password, salt):
        return scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

    def sign_in(self, username, password, *, create=False):
        username = username.strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,39}', username) or not 10 <= len(password) <= 128:
            raise ReviewError('Use a 3–40 character username and a password of 10–128 characters.', 422)
        with self.lock:
            now = self.clock()
            # Aggregate backoff as well as account backoff limits username spraying.
            for key in ('*', username):
                attempts = [t for t in self.failures.get(key, []) if t > now - 300]
                self.failures[key] = attempts
                if len(attempts) >= (30 if key == '*' else 8):
                    raise ReviewError('Too many attempts. Wait five minutes and try again.', 429)
            with self.transaction(write=create) as state:
                account = state['accounts'].get(username)
                if create:
                    if account:
                        raise ReviewError('This username is unavailable. Choose another or sign in.', 409)
                    if len(state['accounts']) >= 100:
                        raise ReviewError('Local account capacity reached.', 409)
                    salt = token_hex(16)
                    account = {'id': token_hex(16), 'username': username, 'salt': salt,
                               'password_hash': self.password_hash(password, salt)}
                    state['accounts'][username] = account
                    state['workspaces'][account['id']] = {'sources': [], 'checks': {}, 'entries': {},
                                                          'plans': {}, 'audit': []}
                else:
                    actual = self.password_hash(password, account['salt'] if account else self._dummy_salt)
                    if not account or not compare_digest(actual, account['password_hash']):
                        for key in ('*', username):
                            self.failures[key].append(now)
                        raise ReviewError('Username or password is incorrect.', 401)
            self.failures.pop(username, None)
            self.prune()
            if len(self.sessions) >= 100:
                raise ReviewError('Too many active sessions. Try again later.', 429)
            token = token_urlsafe(32)
            session = Session(account['id'], token_urlsafe(32), now + SESSION_SECONDS)
            self.sessions[sha256(token.encode()).hexdigest()] = session
            return token, session

    def prune(self):
        for key, session in list(self.sessions.items()):
            if session.expires <= self.clock():
                self._end(session)
                self.sessions.pop(key, None)

    def authenticate(self, token):
        with self.lock:
            self.prune()
            session = self.sessions.get(sha256(token.encode()).hexdigest())
            if not session:
                raise ReviewError('Please sign in to continue.', 401)
            return session

    def _end(self, session):
        store = self.stores.get(session.user_id)
        if store and session.principal:
            store.authority.revoke(session.principal)
        from backend.main import CONNECTED_CLIENTS
        for token in session.portals:
            client = CONNECTED_CLIENTS.pop(token, None)
            if client:
                client.close()
        session.google.clear()
        session.oauth.clear()

    def logout(self, session):
        with self.lock:
            self._end(session)
            self.sessions = {k: v for k, v in self.sessions.items() if v is not session}

    def user(self, session):
        with self.transaction() as state:
            return next({'username': a['username'], 'id': a['id']} for a in state['accounts'].values()
                        if a['id'] == session.user_id)

    def store(self, session):
        with self.lock:
            if session.user_id not in self.stores:
                authority = TrustedHostAuthority()
                self.stores[session.user_id] = PersistentReviewStore(
                    authority, self.journal_factory('reviews-' + session.user_id), registry=self.registry)
            return self.stores[session.user_id]

    def enroll_source(self, session, source):
        pair = [source.source_document_id, source.content_sha256]
        with self.transaction(write=True) as state:
            workspace = state['workspaces'][session.user_id]
            if pair not in workspace['sources']:
                if len(workspace['sources']) >= 16:
                    raise ReviewError('This local account supports up to 16 source captures.', 409)
                workspace['sources'].append(pair)
        self.connect_review(session)

    def connect_review(self, session):
        store = self.store(session)
        with self.transaction() as state:
            bindings = state['workspaces'][session.user_id]['sources']
        if bindings:
            if session.principal:
                try:
                    store.authority.require(session.principal,'read')
                    if (session.principal.actor_id == 'student:' + session.user_id and
                            session.principal.source_bindings == frozenset(map(tuple,bindings))):
                        return store
                except ReviewError:
                    pass
                store.authority.revoke(session.principal)
            session.principal, _ = store.authority.issue(
                'student:' + session.user_id, PERMISSIONS, source_bindings=bindings,
                lifetime_minutes=max(1, min(60, int((session.expires - self.clock()) / 60))))
        return store
