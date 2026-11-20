"""Opt-in Windows-session review host; authorization exists only at its console."""
from dataclasses import dataclass, field
from hashlib import sha256
from hmac import compare_digest
from http.cookies import SimpleCookie, CookieError
import json
from pathlib import Path
import re
import secrets
from threading import RLock
import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

from backend.review.api import ReviewBridge
from backend.review.auth import TrustedHostAuthority
from backend.review.models import ReviewError
from backend.review.persistence import PersistentReviewStore, ReviewJournal
from backend.review.windows_security import current_actor
from backend.services.file_extractor import MAX_TEXT_BYTES

HOST = '127.0.0.1:8020'
ORIGIN = 'http://' + HOST
COOKIE = 'stair_local_review'
ROOT = Path(__file__).resolve().parents[2]
PERMISSIONS = frozenset({'read', 'queue', 'assign', 'correct', 'revalidate', 'resolve', 'oversee'})
UUID = r'[0-9a-fA-F-]{36}'
ROUTES = {'GET': re.compile(r'/api/review/(?:session|queue|tasks/' + UUID + r'(?:/(?:snapshot|history))?)\Z'),
          'POST': re.compile(r'/api/review/(?:ingest|session/end|tasks/' + UUID + r'/(?:transition|corrections|revalidate))\Z')}


@dataclass(repr=False)
class Pairing:
    cookie_hash: str
    binding_hash: str
    source: tuple[str, str]
    expires: float
    principal: object = None
    bearer: str = field(default='', repr=False)


class LocalReviewHost:
    def __init__(self, store, actor, *, identity=current_actor, clock=time.time, grant_mode='APPLICATION_OPERATOR'):
        self.store, self.actor, self.identity, self.clock = store, actor, identity, clock
        self.pending = {}; self.channels = {}; self.lock = RLock()
        self.grant_mode = grant_mode
        self.source_manifests = {}

    def request_access(self, payload):
        if set(payload) != {'source_document_id', 'content_sha256'}:
            raise ValueError('Exact source scope required')
        document, capture = payload['source_document_id'], payload['content_sha256']
        if not isinstance(document, str) or not 1 <= len(document) <= 200 or not re.fullmatch('[a-f0-9]{64}', capture):
            raise ValueError('Invalid source')
        with self.lock:
            now = self.clock()
            self.pending = {k: v for k, v in self.pending.items() if v.expires > now}
            if len(self.pending) >= 16:
                raise ValueError('Too many pending requests')
            cookie, binding, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(18)
            record = Pairing(sha256(cookie.encode()).hexdigest(), sha256(binding.encode()).hexdigest(),
                             (document, capture), now + 120)
            self.pending[nonce] = record
            return cookie, {'nonce': nonce, 'binding': binding, 'expires_in': 120,
                            'authorized': False, 'source_document_id': document, 'content_sha256': capture}

    def authorize_from_console(self, nonce, source_path):
        """Never exposed as an HTTP route. Called only by interactive host stdin."""
        if self.identity() != self.actor:
            raise ValueError('Windows logon changed')
        source = Path(source_path).resolve(strict=True)
        limit = MAX_TEXT_BYTES if source.suffix.lower() == '.txt' else 20 * 1024 * 1024
        if source.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.pdf', '.txt', '.html'} or not 0 < source.stat().st_size <= limit:
            raise ValueError('An original supported timetable file is required')
        raw = source.read_bytes()
        capture = sha256(raw).hexdigest()
        with self.lock:
            record = self.pending.get(nonce)
            if not record or record.expires <= self.clock() or not compare_digest(capture, record.source[1]):
                raise ValueError('Pending request expired or original file hash differs')
            pages = 1
            if source.suffix.lower() == '.pdf':
                from io import BytesIO
                import pdfplumber
                try:
                    with pdfplumber.open(BytesIO(raw)) as pdf:
                        pages = len(pdf.pages)
                except Exception:
                    raise ValueError('Original source pages could not be established') from None
                if not 1 <= pages <= 100:raise ValueError('Source page limit exceeded')
            principal, bearer = self.store.authority.issue(self.actor, PERMISSIONS,
                source_bindings={record.source}, mode=self.grant_mode, lifetime_minutes=30)
            self.source_manifests[record.source] = {'source_document_id':record.source[0], 'content_sha256':capture, 'page_count':pages}
            record.principal, record.bearer = principal, bearer
            # A new explicit pairing replaces old browser access, not saved history.
            for old in self.channels.values():
                self.store.authority.revoke(old.principal)
            self.channels = {record.cookie_hash: record}
            self.pending.pop(nonce)

    def authenticate(self, cookie, binding):
        if not all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', v) for v in (cookie, binding)):
            raise ValueError('Missing browser channel')
        with self.lock:
            record = self.channels.get(sha256(cookie.encode()).hexdigest())
            if not record or not compare_digest(record.binding_hash, sha256(binding.encode()).hexdigest()):
                raise ValueError('Browser channel refused')
            self.store.authority.require(record.principal, 'read')
            return record

    def end(self, record):
        with self.lock:
            self.store.authority.revoke(record.principal)
            self.channels.pop(record.cookie_hash, None)
            record.bearer = ''


class LocalBoundary:
    def __init__(self, app, host):
        self.app, self.host = app, host

    @staticmethod
    def headers(scope, *, host=HOST):
        raw = scope.get('headers', [])
        critical = {b'host', b'origin', b'referer', b'cookie', b'authorization', b'x-stair-review-binding', b'x-stair-independent-binding', b'x-stair-calendar-binding',
                    b'sec-fetch-site', b'sec-fetch-mode', b'sec-fetch-dest'}
        if any(sum(k.lower() == name for k, _ in raw) > 1 for name in critical):
            raise ValueError('Duplicate boundary header')
        if any(k.lower().startswith(b'x-forwarded-') or k.lower() in {b'forwarded', b'x-real-ip'} for k, _ in raw):
            raise ValueError('Forwarding refused')
        h = {k.lower().decode('latin1'): v.decode('latin1') for k, v in raw}
        if (scope.get('client') or ('',))[0] != '127.0.0.1' or h.get('host') != host:
            raise ValueError('Exact loopback boundary required')
        if b'%' in scope.get('raw_path', b'') or '\\' in scope['path'] or '//' in scope['path']:
            raise ValueError('Encoded route refused')
        return h

    @staticmethod
    def browser(h, method, *, host=HOST, origin=ORIGIN):
        from urllib.parse import urlsplit
        if h.get('sec-fetch-site') != 'same-origin' or h.get('sec-fetch-mode') not in {'cors', 'same-origin'} or h.get('sec-fetch-dest') != 'empty':
            raise ValueError('Same-origin browser request required')
        if h.get('origin') == origin:
            return
        ref = urlsplit(h.get('referer', ''))
        if method != 'GET' or 'origin' in h or ref.scheme != 'http' or ref.netloc != host or ref.username or ref.password:
            raise ValueError('Origin refused')

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        try:
            h = self.headers(scope); path, method = scope['path'], scope['method']
            if 'authorization' in h:
                raise ValueError('Direct credentials/unrelated identity refused')
            if path.startswith('/api/intranet/'):
                # Institutional sign-in is an explicit, separate operation: it
                # never authorizes local correction or independent verification.
                allowed = method == 'POST' and path in {
                    '/api/intranet/connect', '/api/intranet/timetable', '/api/intranet/disconnect'}
                if (not allowed or scope.get('query_string') or
                        h.get('content-type', '').split(';')[0].strip() != 'application/json' or
                        any(name in h for name in ('x-stair-review-binding', 'x-stair-independent-binding', 'x-stair-calendar-binding'))):
                    raise ValueError('Institutional operation refused')
            if path.startswith('/api/') or path.startswith('/__local_review__/'):
                self.browser(h, method)
            independent=getattr(self.host,'independent',None)
            if path == '/__local_review__/independent-pending' and method=='POST':
                if independent is None or scope.get('query_string') or h.get('content-type','').split(';')[0]!='application/json':raise ValueError('Reviewer pairing refused')
                data=bytearray()
                while True:
                    chunk=await receive()
                    if chunk['type']!='http.request':raise ValueError('Invalid request')
                    data.extend(chunk.get('body',b''))
                    if len(data)>512:raise ValueError('Pairing limit')
                    if not chunk.get('more_body'):break
                payload=json.loads(data)
                if set(payload)!={'invitation'}:raise ValueError('Exact invitation required')
                cookie,result=independent.request(payload['invitation'])
                from backend.review.independent import COOKIE as reviewer_cookie
                response=JSONResponse(result,headers={'Cache-Control':'no-store'})
                response.set_cookie(reviewer_cookie,cookie,httponly=True,samesite='strict',path='/api/independent-review',max_age=900)
                return await response(scope,receive,send)
            if path.startswith('/api/independent-review'):
                if independent is None or scope.get('query_string'):raise ValueError('Reviewer route refused')
                allowed=(method=='GET' and re.fullmatch(r'/api/independent-review/(?:current|source/[1-9][0-9]*)',path)) or (method=='POST' and path in {'/api/independent-review/verify','/api/independent-review/analyze','/api/independent-review/end'})
                if not allowed:raise ValueError('Reviewer route refused')
                from backend.review.independent import COOKIE as reviewer_cookie
                cookies=SimpleCookie();cookies.load(h.get('cookie',''))
                record=independent.authenticate(cookies[reviewer_cookie].value,h.get('x-stair-independent-binding'))
                forwarded=dict(scope);forwarded['state']={**scope.get('state',{}),'independent_record':record}
                async def no_cache(message):
                    if message['type']=='http.response.start':
                        message=dict(message);message['headers']=[(k,v) for k,v in message.get('headers',[]) if k.lower() not in {b'cache-control',b'pragma'}]+[(b'cache-control',b'no-store'),(b'pragma',b'no-cache')]
                    await send(message)
                return await self.app(forwarded,receive,no_cache)
            if path == '/__local_review__/pending' and method == 'POST':
                if scope.get('query_string') or h.get('content-type', '').split(';')[0] != 'application/json':
                    raise ValueError('Invalid pairing request')
                body = bytearray()
                while True:
                    message = await receive()
                    if message['type'] != 'http.request':
                        raise ValueError('Invalid body')
                    body.extend(message.get('body', b''))
                    if len(body) > 1024:
                        raise ValueError('Pairing request too large')
                    if not message.get('more_body'):
                        break
                cookie, result = self.host.request_access(json.loads(body))
                response = JSONResponse(result, headers={'Cache-Control': 'no-store'})
                response.set_cookie(COOKIE, cookie, httponly=True, samesite='strict', path='/api/review', max_age=1800)
                return await response(scope, receive, send)
            if path.startswith('/__local_review__/'):
                raise ValueError('No HTTP authorization route')
            if path.startswith('/api/review/'):
                check_route = (method == 'GET' and re.fullmatch(r'/api/review/schedule-checks/(?:for-task/)?' + UUID + r'(?:/(?:draft|available))?', path)) or (method == 'POST' and re.fullmatch(r'/api/review/schedule-checks/(?:start|' + UUID + r'/(?:primary|verify|analyze|temporal|draft|source-page/[1-9][0-9]*))', path))
                if not check_route and not ROUTES.get(method, re.compile(r'(?!)')).fullmatch(path):
                    raise ValueError('Review route refused')
                query = scope.get('query_string', b'')
                if query and (method != 'GET' or path != '/api/review/queue' or
                        not re.fullmatch(rb'(?:offset|limit)=\d+(?:&(?:offset|limit)=\d+)?', query)):
                    raise ValueError('Review query refused')
                cookies = SimpleCookie(); cookies.load(h.get('cookie', ''))
                record = self.host.authenticate(cookies[COOKIE].value, h.get('x-stair-review-binding'))
                forwarded = dict(scope)
                forwarded['headers'] = [(k, v) for k, v in scope['headers'] if k.lower() not in {b'cookie', b'x-stair-review-binding'}]
                forwarded['headers'].append((b'authorization', ('Bearer ' + record.bearer).encode()))
                async def safe_send(message):
                    if message['type'] == 'http.response.start':
                        message = dict(message)
                        message['headers'] = [(k, v) for k, v in message.get('headers', []) if k.lower() not in {b'cache-control', b'pragma'}]
                        message['headers'] += [(b'cache-control', b'no-store'), (b'pragma', b'no-cache')]
                        if path.endswith('/session/end') and message['status'] == 200:
                            self.host.end(record)
                            response = JSONResponse({}); response.delete_cookie(COOKIE, path='/api/review', httponly=True, samesite='strict')
                            message['headers'] += [(k, v) for k, v in response.raw_headers if k == b'set-cookie']
                    await send(message)
                return await self.app(forwarded, receive, safe_send)
            async def current_assets(message):
                if message['type']=='http.response.start' and (path=='/' or path.startswith('/api/intranet/') or path.endswith(('.js','.css','.html'))):
                    message=dict(message)
                    message['headers']=[(k,v) for k,v in message.get('headers',[]) if k.lower() not in {b'cache-control',b'pragma'}]
                    message['headers'] += [(b'cache-control',b'no-store'),(b'pragma',b'no-cache')]
                await send(message)
            return await self.app(scope, receive, current_assets)
        except (ValueError, KeyError, TypeError, CookieError, UnicodeError, ReviewError):
            return await JSONResponse({'detail': 'LOCAL_REVIEW_AUTHORIZATION_REQUIRED'}, 403,
                                      headers={'Cache-Control': 'no-store'})(scope, receive, send)


def build_local_app(directory,*,registry=None):
    actor = current_actor()
    authority = TrustedHostAuthority()
    store = PersistentReviewStore(authority, ReviewJournal(directory, actor),registry=registry)
    host = LocalReviewHost(store, actor)
    return create_host_app(host), host


def create_host_app(host):
    """Compose existing routes around an in-process trusted host, not a grant API."""
    from backend import main
    app = FastAPI(title='STAIR local Windows review')
    app.state.m5_review_bridge = ReviewBridge(host.store, allowed_origins=(ORIGIN,))
    if getattr(host.store,'registry',None) is not None:app.state.m5_operating_registry=host.store.registry
    from backend.analysis.schedule_checks import ScheduleChecks
    def check_journal():
        original = host.store.journal
        return ReviewJournal(original.directory.with_name(original.directory.name + '-ScheduleChecks'), host.actor)
    app.state.m7_schedule_checks = ScheduleChecks(host.store, check_journal, lambda source: host.source_manifests.get((source.source_document_id,source.content_sha256)))
    from backend.review.independent import IndependentAccess
    from backend.review.independent_api import router as independent_router
    host.independent=IndependentAccess(host,app.state.m7_schedule_checks)
    app.state.independent_access=host.independent
    app.include_router(independent_router)
    # Host assets must precede the normal API root route.
    @app.get('/')
    async def index():
        html = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
        return HTMLResponse(html.replace('</body>', '<script src="/js/local-review-host.js" defer></script></body>'),
                            headers={'Cache-Control': 'no-store'})
    @app.get('/js/app.js')
    async def app_script():
        source = (ROOT / 'frontend/js/app.js').read_text(encoding='utf-8')
        return Response(source.replace('const API_BASE = "http://127.0.0.1:8000";', 'const API_BASE = "' + ORIGIN + '";'),
                        media_type='application/javascript', headers={'Cache-Control': 'no-store'})
    app.include_router(main.app.router)
    app.mount('/', StaticFiles(directory=ROOT / 'frontend'), name='frontend')
    app.add_middleware(LocalBoundary, host=host)
    return app
