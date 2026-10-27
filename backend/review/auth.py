"""Explicit in-process trusted-host capability boundary; not university authentication."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
import re
from threading import RLock
from uuid import uuid4

from backend.review.models import ReviewError

PERMISSIONS=frozenset({'read','queue','assign','correct','revalidate','resolve','oversee'})


def utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class HostPrincipal:
    grant_id: str
    actor_id: str
    permissions: frozenset[str]
    expires_at: datetime
    source_bindings: frozenset[tuple[str,str]] | None = None
    mode: str = 'APPLICATION_OPERATOR'


class TrustedHostAuthority:
    """Only trusted host bootstrap issues grants. No HTTP minting or actor-ID login."""
    def __init__(self, clock=utc_now, *, test_only_source_bindings=()):
        self.clock=clock
        self._grants={}
        self._tokens={}
        self._lock=RLock()
        # Trusted host bootstrap declares exact invented fixtures. Browser input
        # cannot label an arbitrary university capture TEST_ONLY.
        self._test_only_bindings=frozenset(tuple(pair) for pair in test_only_source_bindings)

    def issue(self, actor_id, permissions, *, lifetime_minutes=60, source_bindings=None, mode='APPLICATION_OPERATOR'):
        if not actor_id or not actor_id.strip() or not set(permissions)<=PERMISSIONS or not 0<lifetime_minutes<=60:
            raise ValueError('Explicit actor identity, scoped permissions and bounded expiry required')
        bindings=None if source_bindings is None else frozenset(tuple(pair) for pair in source_bindings)
        if mode not in {'APPLICATION_OPERATOR','TEST_ONLY'} or (mode=='TEST_ONLY' and not bindings):
            raise ValueError('An explicit mode and bounded synthetic capture scope are required')
        if bindings is not None and (not 1<=len(bindings)<=16 or any(len(pair)!=2 or not isinstance(pair[0],str)
                or not 1<=len(pair[0])<=200 or not isinstance(pair[1],str) or not re.fullmatch('[a-f0-9]{64}',pair[1]) for pair in bindings)):
            raise ValueError('Source scope requires exact document IDs and capture hashes')
        if mode=='TEST_ONLY' and not bindings<=self._test_only_bindings:
            raise ValueError('TEST_ONLY grants require host-declared synthetic capture bindings')
        grant=HostPrincipal(str(uuid4()),actor_id,frozenset(permissions),self.clock()+timedelta(minutes=lifetime_minutes),bindings,mode)
        token=token_urlsafe(32)
        with self._lock:
            self._grants[grant.grant_id]=grant
            self._tokens[sha256(token.encode()).hexdigest()]=grant.grant_id
        return grant,token

    def require(self, principal, permission):
        with self._lock:
            if not isinstance(principal,HostPrincipal) or self._grants.get(principal.grant_id) is not principal or principal.expires_at<=self.clock():
                raise ReviewError('UNAUTHORIZED_REVIEW',403)
            if permission not in principal.permissions:
                raise ReviewError('REVIEW_PERMISSION_DENIED',403)
        return principal

    def authenticate(self, token):
        with self._lock:
            key=self._tokens.get(sha256(token.encode()).hexdigest())
            grant=self._grants.get(key)
        if grant is None:
            raise ReviewError('UNAUTHORIZED_REVIEW',403)
        return grant

    @staticmethod
    def allows_source(principal, source):
        # None preserves the existing in-process legacy host contract. New UI
        # enrollment requires an explicitly scoped grant; no grant is auto-issued.
        return principal.source_bindings is None or (source.source_document_id,source.content_sha256) in principal.source_bindings

    def require_source(self, principal, source):
        self.require(principal,'read')
        if not self.allows_source(principal,source):
            raise ReviewError('REVIEW_SOURCE_SCOPE_DENIED',403)

    def actor_is_authorized(self, actor_id, permission, source=None):
        with self._lock:
            return any(g.actor_id==actor_id and permission in g.permissions and g.expires_at>self.clock()
                       and (source is None or self.allows_source(g,source)) for g in self._grants.values())

    def revoke(self, principal):
        with self._lock:
            self._grants.pop(principal.grant_id,None)
