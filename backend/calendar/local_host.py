"""Opt-in Windows-owner calendar demo. No uploads, review records or providers."""
from contextlib import ExitStack
from hashlib import sha256
from hmac import compare_digest
from http.cookies import SimpleCookie, CookieError
from itertools import count
import json
from pathlib import Path
import re
from threading import RLock
import time
from uuid import uuid5, NAMESPACE_URL

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response, JSONResponse
from backend.calendar.access import CalendarAccess, PERMISSIONS
from backend.calendar.models import CalendarError, CalendarInput
from backend.calendar.sandbox import LocalSandbox
from backend.calendar.testing import SyntheticFixtureAuthority
from backend.calendar.workflow import TestOnlyCalendarBridge
from backend.calendar.workflow_api import router
from backend.reconstruction.models import digest
from backend.review.auth import TrustedHostAuthority
from backend.review.local_host import LocalBoundary, Pairing
from backend.review.models import ReviewError
from backend.review.secure_directory import WindowsDirectories, _chain, _tree, writer_lock
from backend.review.windows_security import current_actor

ROOT = Path(__file__).resolve().parents[2]
HOST = '127.0.0.1:8022'
ORIGIN = 'http://' + HOST
COOKIE = 'stair_calendar_demo'
PREFIX = '/api/calendar/v1/test-only'
GET = re.compile(re.escape(PREFIX) + r'/(?:session|history|state)\Z')
POST = re.compile(re.escape(PREFIX) + r'/(?:preview|approve|execute|reconcile|revoke-approval|session/end)\Z')


def fixture_catalogue(sandbox):
    # Versioned deterministic IDs keep this fixed invented catalogue bound to
    # the same source/revisions after restart. Defaults elsewhere stay random.
    serial = count()
    factory = SyntheticFixtureAuthority(sandbox, identity_factory=lambda: uuid5(NAMESPACE_URL, 'stair-local-fr6-v1/' + str(next(serial))))
    common = {'day_of_week': 1, 'date': '2033-02-07', 'source_timezone': 'Etc/UTC'}
    base = factory.make(capture_id='synthetic:local-fr6-v1', events=[
        {**common, 'course_code': 'SYN101', 'course_name': 'Invented Networks', 'session_type': 'Lecture', 'start_time': '09:00', 'end_time': '10:00', 'location': 'DEMO ROOM A', 'session_id': 'synthetic-session:local-1'},
        {**common, 'course_code': 'SYN202', 'course_name': 'Invented Computing', 'session_type': 'Tutorial', 'start_time': '11:00', 'end_time': '12:00', 'location': 'DEMO ROOM B', 'session_id': 'synthetic-session:local-2'}])
    update = factory.make(previous=base, changes={'location': 'DEMO ROOM C'})
    invalid_zone = factory.make(capture_id='synthetic:local-fr6-zone-v1', events=[
        {**common, 'source_timezone': None, 'course_code': 'SYN303', 'start_time': '13:00', 'end_time': '14:00', 'session_id': 'synthetic-session:blocked-zone'}])
    invalid_repeat = factory.make(capture_id='synthetic:local-fr6-repeat-v1', events=[
        {**common, 'course_code': 'SYN404', 'start_time': '15:00', 'end_time': '16:00', 'session_id': 'synthetic-session:blocked-repeat',
         'recurrence': {'frequency': 'WEEKLY', 'interval': 1}}])
    return {'base': ('Original sample: two invented classes', base),
            'update': ('Room change example: DEMO ROOM A → DEMO ROOM C', update),
            'blocked-zone': ('Blocked example: timezone not confirmed', invalid_zone),
            'blocked-repeat': ('Blocked example: repeat schedule incomplete', invalid_repeat)}


def private_sandbox_directory(directory):
    """Keep the existing Journal isolation root; protect only its own test tree."""
    directory = Path(directory)
    allowed = ROOT / 'data' / 'temp'
    if not directory.is_absolute() or '..' in directory.parts or directory.parent != allowed or not directory.name.startswith('m6-test-'):
        raise CalendarError('SYNTHETIC_SANDBOX_PATH_REQUIRED', 422)
    allowed.mkdir(parents=True, exist_ok=True)
    api = WindowsDirectories()
    with ExitStack() as held, api.descriptor() as sd:
        for parent in _chain(allowed):
            _, actual = held.enter_context(api.open_directory(parent))
            if actual != parent:
                raise CalendarError('SANDBOX_PATH_NOT_ISOLATED', 422)
        api.create(directory, sd)
        _, actual = held.enter_context(api.open_directory(directory))
        if actual != directory:
            raise CalendarError('SANDBOX_PATH_NOT_ISOLATED', 422)
        api.restrict(directory, sd)
        api.create(directory / 'records', sd)
        held.enter_context(api.open_directory(directory / 'records'))
        with writer_lock(directory):
            for path in _tree(directory):
                api.restrict(path, sd)
    return directory


class LocalCalendarHost:
    def __init__(self, bridge, actor, *, identity=current_actor, clock=time.time):
        self.bridge, self.actor, self.identity, self.clock = bridge, actor, identity, clock
        self.authority = bridge.sandbox.access.host_authority
        self.bindings = frozenset((source.source_document_id, source.content_sha256)
            for _, raw in bridge.catalogue.values()
            for source in [CalendarInput.model_validate_json(raw).packet.source])
        self.catalogue_digest = digest(dict(bridge.catalogue))
        self.pending, self.channels, self.lock = {}, {}, RLock()

    def request_access(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'acknowledge_test_only'} or payload['acknowledge_test_only'] is not True:
            raise ValueError('Explicit test-only acknowledgement required')
        with self.lock:
            now = self.clock()
            self.pending = {key: value for key, value in self.pending.items() if value.expires > now}
            if len(self.pending) >= 16:
                raise ValueError('Too many pending requests')
            import secrets
            cookie, binding, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(18)
            record = Pairing(sha256(cookie.encode()).hexdigest(), sha256(binding.encode()).hexdigest(),
                             ('synthetic:local-fr6-catalogue-v1', self.catalogue_digest), now + 120)
            self.pending[nonce] = record
            return cookie, {'nonce': nonce, 'binding': binding, 'expires_in': 120, 'authorized': False}

    def authorize_from_console(self, nonce):
        if self.identity() != self.actor:
            raise ValueError('Windows owner changed')
        with self.lock:
            record = self.pending.get(nonce)
            if not record or record.expires <= self.clock() or record.source[1] != self.catalogue_digest:
                self.pending.pop(nonce, None)
                raise ValueError('Request expired or catalogue changed')
            principal, bearer = self.authority.issue(self.actor, {'read'}, mode='TEST_ONLY',
                source_bindings=self.bindings, lifetime_minutes=30)
            self.bridge.sandbox.access.enrol(principal, self.bridge.sandbox.sandbox_id, PERMISSIONS)
            record.principal, record.bearer = principal, bearer
            for old in self.channels.values():
                self.authority.revoke(old.principal)
            self.channels = {record.cookie_hash: record}
            self.pending.pop(nonce)

    def authenticate(self, cookie, binding):
        if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', v) for v in (cookie, binding)):
            raise ValueError('Missing browser channel')
        with self.lock:
            record = self.channels.get(sha256(cookie.encode()).hexdigest())
            if not record or not compare_digest(record.binding_hash, sha256(binding.encode()).hexdigest()):
                raise ValueError('Browser channel refused')
            self.bridge.require(record.principal)
            return record

    def end(self, record):
        with self.lock:
            self.authority.revoke(record.principal)
            self.channels.pop(record.cookie_hash, None)
            record.bearer = ''


class CalendarBoundary:
    def __init__(self, app, host):
        self.app, self.host = app, host

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        try:
            h = LocalBoundary.headers(scope, host=HOST)
            path, method = scope['path'], scope['method']
            if 'authorization' in h or scope.get('query_string'):
                raise ValueError('Direct bearer and queries refused')
            if path.startswith(('/api/', '/__local_calendar__/')):
                LocalBoundary.browser(h, method, host=HOST, origin=ORIGIN)
            if path == '/__local_calendar__/pending' and method == 'POST':
                if h.get('content-type', '').split(';')[0] != 'application/json':
                    raise ValueError('JSON required')
                body = bytearray()
                while True:
                    message = await receive()
                    if message['type'] != 'http.request':
                        raise ValueError('Invalid body')
                    body.extend(message.get('body', b''))
                    if len(body) > 512:
                        raise ValueError('Request too large')
                    if not message.get('more_body'):
                        break
                cookie, result = self.host.request_access(json.loads(body))
                response = JSONResponse(result, headers={'Cache-Control': 'no-store'})
                response.set_cookie(COOKIE, cookie, httponly=True, samesite='strict', path=PREFIX, max_age=1800)
                return await response(scope, receive, send)
            if path.startswith('/__local_calendar__/'):
                raise ValueError('No HTTP authorization endpoint')
            if path.startswith('/api/'):
                if not (GET if method == 'GET' else POST if method == 'POST' else re.compile(r'(?!)')).fullmatch(path):
                    raise ValueError('Sandbox-only route required')
                cookies = SimpleCookie(); cookies.load(h.get('cookie', ''))
                record = self.host.authenticate(cookies[COOKIE].value, h.get('x-stair-calendar-binding'))
                forwarded = dict(scope)
                forwarded['headers'] = [(k, v) for k, v in scope['headers'] if k.lower() not in {b'cookie', b'x-stair-calendar-binding'}]
                forwarded['headers'].append((b'authorization', ('Bearer ' + record.bearer).encode()))
                async def safe_send(message):
                    if message['type'] == 'http.response.start':
                        message = dict(message)
                        message['headers'] = [(k, v) for k, v in message.get('headers', []) if k.lower() not in {b'cache-control', b'pragma'}]
                        message['headers'] += [(b'cache-control', b'no-store'), (b'pragma', b'no-cache')]
                        if path.endswith('/session/end') and message['status'] == 200:
                            self.host.end(record)
                            response = JSONResponse({}); response.delete_cookie(COOKIE, path=PREFIX, httponly=True, samesite='strict')
                            message['headers'] += [(k, v) for k, v in response.raw_headers if k == b'set-cookie']
                    await send(message)
                return await self.app(forwarded, receive, safe_send)
            return await self.app(scope, receive, send)
        except (ValueError, KeyError, TypeError, CookieError, UnicodeError, ReviewError, CalendarError):
            return await JSONResponse({'detail': 'LOCAL_CALENDAR_AUTHORIZATION_REQUIRED'}, status_code=403,
                headers={'Cache-Control': 'no-store'})(scope, receive, send)


def build_local_calendar(directory=None):
    actor = current_actor()
    directory = directory or ROOT / 'data' / 'temp' / ('m6-test-local-calendar-' + sha256(actor.encode()).hexdigest()[:16])
    directory = private_sandbox_directory(directory)
    initial = TrustedHostAuthority()
    sandbox = LocalSandbox(directory, 'test:local-student-calendar-v1', CalendarAccess(initial), test_only=True)
    catalogue = fixture_catalogue(sandbox)
    bindings = {(bundle.packet.source.source_document_id, bundle.packet.source.content_sha256) for _, bundle in catalogue.values()}
    sandbox.access = CalendarAccess(TrustedHostAuthority(test_only_source_bindings=bindings))
    bridge = TestOnlyCalendarBridge(sandbox, catalogue, allowed_origins=(ORIGIN,), allow_failure_simulation=False)
    host = LocalCalendarHost(bridge, actor)
    return create_calendar_app(host), host


def create_calendar_app(host):
    app = FastAPI(title='STAIR TEST_ONLY local calendar', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.s3c_calendar_bridge = host.bridge
    app.include_router(router, prefix='/api/calendar/v1')
    @app.get('/')
    async def index():
        return HTMLResponse((ROOT / 'frontend/calendar-sandbox.html').read_text(encoding='utf-8'), headers={'Cache-Control': 'no-store'})
    for url, name, mime in [('/js/sandbox.js', 'frontend/js/sandbox.js', 'application/javascript'),
            ('/js/local-calendar-host.js', 'frontend/js/local-calendar-host.js', 'application/javascript'),
            ('/styles.css', 'frontend/styles.css', 'text/css')]:
        def make_asset(filename, media_type):
            def asset():
                return Response((ROOT / filename).read_text(encoding='utf-8'), media_type=media_type, headers={'Cache-Control': 'no-store'})
            return asset
        app.add_api_route(url, make_asset(name, mime), methods=['GET'], include_in_schema=False)
    app.add_middleware(CalendarBoundary, host=host)
    return app
