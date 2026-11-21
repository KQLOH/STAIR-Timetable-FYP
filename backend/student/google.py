"""Optional Google OAuth and bounded writes to one configured secondary calendar.

Tokens stay in the login session. No automatic retries, primary-calendar writes,
event deletion, guest invitations or token persistence.
"""
from base64 import urlsafe_b64encode
from hashlib import sha256
import os
from secrets import token_urlsafe, compare_digest
from urllib.parse import urlencode, quote

import requests

from backend.review.models import ReviewError

SCOPES = ('https://www.googleapis.com/auth/calendar.events '
          'https://www.googleapis.com/auth/calendar.calendarlist.readonly')


class GoogleCalendar:
    def __init__(self, session, origin, *, clock, transport=None, config=None):
        self.session, self.origin, self.clock = session, origin, clock
        self.http = transport or requests
        config = os.environ if config is None else config
        self.client_id = config.get('STAIR_GOOGLE_CLIENT_ID', '')
        self.secret = config.get('STAIR_GOOGLE_CLIENT_SECRET', '')
        self.calendar_id = config.get('STAIR_GOOGLE_TEST_CALENDAR_ID', '')
        self.target = 'google:' + self.calendar_id
        self.redirect_uri = origin + '/api/student/google/callback'

    @property
    def configured(self):
        return bool(self.client_id and self.secret and self.calendar_id and
                    self.calendar_id.lower() != 'primary' and len(self.calendar_id) <= 250)

    def status(self):
        connected = bool(self.session.google and self.session.google.get('verified_target') == self.calendar_id)
        return {'configured': self.configured, 'connected': connected,
                'target': self.target if self.configured else None,
                'label': self.session.google.get('label', 'Dedicated Google test calendar'),
                'message': 'Google is connected. Changes still require your preview and approval.' if connected else
                'Connect your Google account to use the configured test calendar.' if self.configured
                else 'Google is not configured on this computer. You can use your private local test calendar.'}

    def start(self):
        if not self.configured:
            raise ReviewError('Google Calendar setup is required on this computer.', 409)
        state, verifier = token_urlsafe(32), token_urlsafe(48)
        self.session.oauth = {'state': state, 'verifier': verifier, 'expires': self.clock() + 600}
        challenge = urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip('=')
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': self.client_id, 'redirect_uri': self.redirect_uri, 'response_type': 'code',
            'scope': SCOPES, 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256',
            'access_type': 'offline', 'prompt': 'consent'})

    def callback(self, state, code):
        pending = self.session.oauth
        self.session.oauth = {}
        if not pending or pending['expires'] <= self.clock() or not compare_digest(state, pending['state']):
            raise ReviewError('Google sign-in expired or could not be verified. Connect again.', 403)
        if not code or len(code) > 4096:
            raise ReviewError('Google authorization was cancelled.', 422)
        tokens = self._token({'code': code, 'code_verifier': pending['verifier'], 'grant_type': 'authorization_code',
                              'redirect_uri': self.redirect_uri})
        if not isinstance(tokens.get('scope'), str):
            raise ReviewError('Google did not return valid authorization permissions. Connect again.', 403)
        granted = set(tokens['scope'].split())
        if not set(SCOPES.split()) <= granted:
            raise ReviewError('Google did not grant the required calendar permissions.', 403)
        self.session.google = tokens
        try:
            # A host-configured ID alone is insufficient: refuse primary or read-only targets.
            meta = self.request('GET', '/users/me/calendarList/' + quote(self.calendar_id, safe=''))
            if meta.get('primary') or meta.get('accessRole') not in {'owner', 'writer'} or meta.get('deleted'):
                raise ReviewError('Choose a writable secondary test calendar in the server configuration.', 403)
            self.session.google['label'] = str(meta.get('summary', 'Google test calendar'))[:200]
            self.session.google['verified_target'] = self.calendar_id
        except Exception:
            self.session.google.clear()
            raise

    def _token(self, data):
        try:
            response = self.http.post('https://oauth2.googleapis.com/token', data={**data,
                'client_id': self.client_id, 'client_secret': self.secret}, timeout=20, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError('token response')
            result = response.json()
            if (not isinstance(result, dict) or not isinstance(result.get('access_token'), str) or
                    not result['access_token'] or not 60 < int(result.get('expires_in', 0)) <= 86400 or
                    str(result.get('token_type', 'Bearer')).lower() != 'bearer'):
                raise ValueError('token fields')
            result['expires_at'] = self.clock() + int(result['expires_in']) - 60
            return result
        except (requests.RequestException, ValueError, TypeError):
            raise ReviewError('Google authorization could not complete. Connect again.', 502) from None

    def request(self, method, path, *, body=None, etag=None, allow_missing=False):
        tokens = self.session.google
        if not self.configured or not tokens:
            raise ReviewError('Connect Google Calendar before continuing.', 409)
        if tokens['expires_at'] <= self.clock():
            if not tokens.get('refresh_token'):
                raise ReviewError('Google authorization expired. Connect again.', 401)
            refreshed = self._token({'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']})
            tokens.update(refreshed)
        headers = {'Authorization': 'Bearer ' + tokens['access_token']}
        if etag:
            headers['If-Match'] = etag
        try:
            response = self.http.request(method, 'https://www.googleapis.com/calendar/v3' + path,
                headers=headers, json=body, timeout=20, allow_redirects=False)
            if allow_missing and response.status_code == 404:
                return None
            if response.status_code not in {200, 201}:
                raise ValueError('calendar request')
            result = response.json()
            if not isinstance(result, dict): raise ValueError('Invalid calendar response')
            return result
        except (requests.RequestException, ValueError, TypeError):
            raise ReviewError('Google did not confirm this operation. Check the result before trying a fresh preview.', 502) from None

    def read(self, key):
        if self.session.google.get('verified_target') != self.calendar_id:
            raise ReviewError('Reconnect and verify the configured Google target.', 409)
        return self.request('GET', self._path(key), allow_missing=True)

    def _path(self, key=None):
        path = '/calendars/' + quote(self.calendar_id, safe='') + '/events'
        return path + ('/' + key if key else '')

    def owned_entries(self):
        if self.session.google.get('verified_target') != self.calendar_id:
            raise ReviewError('Reconnect and verify the Google target first.', 409)
        result = self.request('GET', self._path() + '?' + urlencode({
            'privateExtendedProperty': 'stairAccount=' + self.session.user_id,
            'maxResults': 2500, 'showDeleted': 'false'}))
        if result.get('nextPageToken'):
            raise ReviewError('This test calendar exceeds the supported planning size.', 409)
        entries = {item['id']: item for item in result.get('items', []) if item.get('status') != 'cancelled'}
        from datetime import datetime
        try:
            for item in entries.values():
                for name in ('start', 'end'):
                    value = datetime.fromisoformat(item[name]['dateTime'])
                    if value.tzinfo is None:
                        raise ValueError('missing offset')
        except (KeyError, ValueError, TypeError):
            raise ReviewError('A saved STAIR event was changed to an unsupported date format in Google. Review the target calendar first.', 409) from None
        return entries

    @staticmethod
    def content(event):
        if event is None:
            return None
        result = {key: event.get(key, '') for key in ('summary', 'location', 'description')}
        for name in ('start', 'end'):
            raw = event.get(name, {})
            result[name] = {k: raw.get(k) for k in ('dateTime', 'timeZone')}
            value = raw.get('dateTime')
            if value:
                from datetime import datetime, timezone
                try:
                    instant = datetime.fromisoformat(value)
                    if instant.tzinfo is None: raise ValueError('Offset required')
                    result[name]['dateTime'] = instant.astimezone(timezone.utc).isoformat()
                except (ValueError, TypeError):
                    raise ReviewError('Google returned an unsupported event time. Inspect the target calendar.', 409) from None
        result['extendedProperties'] = {'private': event.get('extendedProperties', {}).get('private', {})}
        return result

    def apply(self, operation):
        key, desired = operation['key'], operation['body']
        current = self.read(key)
        if self.content(current) == self.content(desired):
            return 'CONFIRMED'
        if current is not None:
            private = current.get('extendedProperties', {}).get('private', {})
            if private.get('stairAccount') != self.session.user_id or private.get('stairKey') != key:
                return 'CHECK_REQUIRED'
        if self.content(current) != operation['before'] or (current and current.get('etag') != operation['etag']):
            return 'CHECK_REQUIRED'
        if operation['action'] == 'CREATE':
            self.request('POST', self._path() + '?sendUpdates=none', body={'id': key, **desired})
        elif operation['action'] == 'UPDATE' and operation['etag']:
            self.request('PATCH', self._path(key) + '?sendUpdates=none', body=desired, etag=operation['etag'])
        else:
            return 'CHECK_REQUIRED'
        return 'CONFIRMED' if self.content(self.read(key)) == self.content(desired) else 'CHECK_REQUIRED'

    def reconcile(self, plan):
        if plan['target'] != self.target:
            raise ReviewError('Reconnect the original Google target to check this attempt.', 409)
        rows = [{'key': op['key'], 'status': 'CONFIRMED' if self.content(self.read(op['key'])) == self.content(op['body'])
                 else 'NOT_MATCHED'} for op in plan['operations']]
        return {'status': 'SUCCEEDED' if all(r['status'] == 'CONFIRMED' for r in rows) else 'CHECK_REQUIRED',
                'operations': rows, 'read_only': True}
