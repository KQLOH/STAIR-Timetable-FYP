"""Dedicated sandbox permissions layered on existing M5 opaque host identities."""
from backend.calendar.models import CalendarError
from backend.review.models import ReviewError

PERMISSIONS={'read','preview','approve','execute','reconcile'}


class CalendarAccess:
    def __init__(self,host_authority):
        self.host_authority=host_authority;self._scopes={}

    def enrol(self,principal,sandbox_id,permissions):
        self.host_authority.require(principal,'read')
        if not set(permissions)<=PERMISSIONS: raise ValueError('Unknown sandbox permission')
        self._scopes[(principal.grant_id,sandbox_id)]=(principal,frozenset(permissions))

    def require(self,principal,sandbox_id,permission):
        try: self.host_authority.require(principal,'read')
        except ReviewError: raise CalendarError('CALENDAR_UNAUTHORIZED',403) from None
        grant=self._scopes.get((principal.grant_id,sandbox_id))
        if not grant or grant[0] is not principal or permission not in grant[1]:
            raise CalendarError('CALENDAR_PERMISSION_DENIED',403)
        return principal

    def permissions_for(self,principal,sandbox_id):
        self.require(principal,sandbox_id,'read')
        return self._scopes[(principal.grant_id,sandbox_id)][1]
