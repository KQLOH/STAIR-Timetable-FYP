"""Opt-in, single-process, same-origin account application using existing APIs."""
from hashlib import sha256
from pathlib import Path
from secrets import compare_digest
from types import SimpleNamespace
import re

from fastapi import FastAPI, Request, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, SecretStr, ValidationError

from backend.models.evidence import ContractModel
from backend.review.api import ReviewBridge
from backend.review.models import ReviewError
from backend.student.accounts import Accounts, COOKIE, SESSION_SECONDS
from backend.student.google import GoogleCalendar
from backend.student import workflow
from backend.student.comparison import Reference, export_source, compare_reference

ROOT = Path(__file__).resolve().parents[2]


class Credentials(ContractModel):
    username: str = Field(min_length=3, max_length=40)
    password: SecretStr = Field(min_length=10, max_length=128)


class PreviewInput(ContractModel):
    task_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    target: str = Field(pattern='^(local|google)$')


class PlanInput(ContractModel):
    plan_id: str = Field(pattern='^[a-f0-9]{32}$')
    confirmed: bool = False


class CompareInput(ContractModel):
    task_id: str = Field(min_length=1, max_length=100)
    reference: Reference


class CompletenessInput(ContractModel):
    task_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    original_pages_checked: bool
    groups_checked: bool
    no_missing_classes: bool
    exceptions_checked: bool


async def bounded_bytes(request, limit):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, 'This file is too large.')
    return bytes(data)


async def _body(request, model, *, max_bytes=2 * 1024 * 1024):
    """Useful field feedback without reflecting credentials or source values."""
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise HTTPException(415, 'Send this form as JSON.')
    raw = await bounded_bytes(request, max_bytes)
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False, include_context=False)[0]
        if first['type'] == 'json_invalid':
            raise HTTPException(422, 'The file is not valid JSON.') from None
        labels = {'username': 'Username', 'password': 'Password', 'first_date': 'First class date',
                  'last_date': 'Last class date', 'excluded_dates': 'Excluded dates', 'timezone': 'Timezone',
                  'source_note': 'Source note', 'checked_original': 'Original-source confirmation',
                  'checked_by': 'Reviewer', 'events': 'Reference events', 'source_sha256': 'Source hash'}
        location = next((labels[p] for p in first['loc'] if p in labels), '')
        message = first['msg'].removeprefix('Value error, ')
        raise HTTPException(422, (location + ': ' if location else '') + message) from None


def create_student_app(accounts, *, origin='http://127.0.0.1:8020', google_factory=None, grounded_service=None, google_config=None):
    from backend import main
    app = FastAPI(title='STAIR Student Timetable')
    app.state.student_accounts = accounts
    if grounded_service is not None: app.state.m7_grounded_service = grounded_service
    host = origin.removeprefix('http://')
    google_factory = google_factory or (lambda session: GoogleCalendar(session, origin, clock=accounts.clock, config=google_config))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({'detail': 'Check the required fields and use the requested format.'}, status_code=422)

    @app.exception_handler(ReviewError)
    async def review_error(request, error):
        return JSONResponse({'detail': error.code}, status_code=error.status_code)

    @app.middleware('http')
    async def boundary(request, call_next):
        path = request.url.path
        try:
            if (not request.client or request.client.host != '127.0.0.1' or
                    request.headers.get('host') != host or '\\' in path or '//' in path or
                    b'%' in request.scope.get('raw_path', b'')):
                raise ReviewError('Open this application at ' + origin + '.', 403)
            if path.startswith('/api/'):
                if path.startswith('/api/intranet/') and path not in {
                        '/api/intranet/connect', '/api/intranet/timetable', '/api/intranet/disconnect'}:
                    raise ReviewError('Use your own connected Intranet timetable.', 403)
                if request.method not in {'GET', 'POST'} or 'authorization' in request.headers:
                    raise ReviewError('Unsupported request.', 403)
                callback = path == '/api/student/google/callback' and request.method == 'GET'
                if not callback and (request.headers.get('sec-fetch-site') not in {None, 'same-origin'} or
                        (request.method == 'POST' and request.headers.get('origin') != origin)):
                    raise ReviewError('Please use the STAIR page to perform this action.', 403)
                public = path in {'/api/student/login', '/api/student/register'}
                if not public:
                    session = await run_in_threadpool(accounts.authenticate, request.cookies.get(COOKIE, ''))
                    if request.method == 'POST' and not compare_digest(
                            request.headers.get('x-stair-csrf', ''), session.csrf):
                        raise ReviewError('Your page session changed. Reload and sign in again.', 403)
                    request.state.student_session = session
                    if path.startswith('/api/review/'):
                        if not session.principal:
                            raise ReviewError('Upload and connect your timetable before editing.', 409)
                        request.state.student_review_bridge = ReviewBridge(accounts.store(session), (origin,))
                        request.state.student_review_principal = session.principal
            response = await call_next(request)
        except ReviewError as error:
            response = JSONResponse({'detail': error.code}, status_code=error.status_code)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; frame-src 'self' blob:; "
            "object-src 'self' blob:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        return response

    @app.get('/')
    def index():
        html = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
        html = html.replace('</head>', '<link rel="stylesheet" href="/student.css"><link rel="stylesheet" href="/sandbox-calendar.css"></head>')
        html = html.replace('</body>', '<script src="/js/sandbox-calendar.js" defer></script><script src="/js/student-account.js" defer></script></body>')
        return HTMLResponse(html)

    @app.get('/js/app.js')
    def application_script():
        source = (ROOT / 'frontend/js/app.js').read_text(encoding='utf-8')
        source = source.replace('const API_BASE = "http://127.0.0.1:8000";', 'const API_BASE = "' + origin + '";')
        return Response(source, media_type='application/javascript')

    @app.get('/google-calendar-setup')
    def google_setup():
        from html import escape
        html = (ROOT/'frontend/google-calendar-setup.html').read_text(encoding='utf-8')
        return HTMLResponse(html.replace('STAIR_CALLBACK_URL', escape(origin+'/api/student/google/callback')))

    async def sign_in(request, create):
        data = await _body(request, Credentials, max_bytes=2048)
        old = request.cookies.get(COOKIE)
        token, session = await run_in_threadpool(accounts.sign_in, data.username, data.password.get_secret_value(), create=create)
        if old:
            try: accounts.logout(accounts.authenticate(old))
            except ReviewError: pass
        await run_in_threadpool(accounts.connect_review, session)
        response = JSONResponse({'user': accounts.user(session), 'csrf': session.csrf})
        response.set_cookie(COOKIE, token, httponly=True, samesite='lax', max_age=SESSION_SECONDS, path='/')
        return response

    @app.post('/api/student/login')
    async def login(request: Request):
        return await sign_in(request, False)

    @app.post('/api/student/register')
    async def register(request: Request):
        return await sign_in(request, True)

    @app.get('/api/student/session')
    def session_info(request: Request):
        from backend.student.registered_calendar import authentic_registry_available
        session = request.state.student_session
        return {'user': accounts.user(session), 'csrf': session.csrf, 'expires_at': session.expires,
                'google': google_factory(session).status(),
                'calendar_policy': accounts.calendar_policy,
                'research_accept_available': authentic_registry_available(accounts.registry)}

    @app.post('/api/student/logout')
    def logout(request: Request):
        accounts.logout(request.state.student_session)
        response = JSONResponse({'signed_out': True})
        response.delete_cookie(COOKIE, path='/', httponly=True, samesite='lax')
        return response

    @app.post('/api/student/review/connect')
    def reconnect_review(request: Request):
        session = request.state.student_session
        accounts.connect_review(session)
        return {'connected': session.principal is not None}

    @app.post('/api/student/source')
    async def enroll(request: Request):
        raw = await bounded_bytes(request, 20 * 1024 * 1024)
        source_id = request.headers.get('x-stair-source-id', '')
        checksum = request.headers.get('x-stair-source-sha256', '')
        if not 1 <= len(source_id) <= 200 or not re.fullmatch('[a-f0-9]{64}', checksum) or sha256(raw).hexdigest() != checksum:
            raise HTTPException(422, 'Reselect the exact original timetable file. The file hash did not match.')
        await run_in_threadpool(accounts.enroll_source, request.state.student_session,
                               SimpleNamespace(source_document_id=source_id, content_sha256=checksum))
        return {'connected': True, 'purpose': 'PERSONAL_APPLICATION_REVIEW_NOT_GOLD'}

    # Shadow the ordinary portal routes: connection tokens are also account-bound.
    @app.post('/api/intranet/connect')
    async def intranet_connect(request: Request):
        data = await _body(request, main.TARUMTLoginRequest, max_bytes=4096)
        result = await run_in_threadpool(main.connect_tarumt, data)
        if isinstance(result, dict) and result.get('success'):
            session = request.state.student_session
            session.portals.add(result['connection_token'])
            canonical = result['data'].get('canonical')
            if canonical:
                from backend.models.evidence import SourceDocument
                await run_in_threadpool(accounts.enroll_source, session, SourceDocument.model_validate(canonical['source']))
        return result

    async def portal_operation(request, operation):
        data = await _body(request, main.TARUMTSessionRequest, max_bytes=1024)
        session = request.state.student_session
        if data.token not in session.portals:
            raise HTTPException(403, 'Reconnect your own Intranet session.')
        result = await run_in_threadpool(operation, data)
        if isinstance(result, dict) and result.get('success') and result.get('data', {}).get('canonical'):
            from backend.models.evidence import SourceDocument
            await run_in_threadpool(accounts.enroll_source, session,
                SourceDocument.model_validate(result['data']['canonical']['source']))
        return result

    @app.post('/api/intranet/timetable')
    async def intranet_refresh(request: Request):
        return await portal_operation(request, main.refresh_tarumt_timetable)

    @app.post('/api/intranet/disconnect')
    async def intranet_disconnect(request: Request):
        return await portal_operation(request, main.disconnect_tarumt)

    def serialize_operation(session, function, *args, **kwargs):
        # Serialize planning with account writes and review corrections in this host.
        with accounts.lock, accounts.store(session)._lock:
            return function(accounts, session, *args, **kwargs)

    @app.get('/api/student/workspace')
    def workspace(request: Request):
        session = request.state.student_session
        store, tasks = workflow.current_tasks(accounts, session)
        for task in tasks:
            task['source_sha256'] = store.source(task['task_id'], session.principal).content_sha256
        with accounts.transaction() as state:
            data = state['workspaces'][session.user_id]
            by_task = {task['task_id']: task for task in tasks}
            applied = {item['plan_id']: item['at'] for item in data['audit']
                       if item.get('operation') == 'APPLY_APPROVED_PREVIEW'}
            metadata = {}
            for plan in sorted(data['plans'].values(), key=lambda item: item['created']):
                if plan['target'] != 'local' or not plan.get('approved') or (plan.get('outcome') or {}).get('status') != 'SUCCEEDED':
                    continue
                for operation in plan['operations']:
                    if data['entries'].get(operation['key']) != operation['body']:
                        continue
                    task = by_task.get(operation['task_id'])
                    metadata[operation['key']] = {'task_id': operation['task_id'],
                        'revision_id': operation['revision_id'], 'saved_at': applied.get(plan['id']),
                        'calendar_policy': plan.get('calendar_policy', 'human-confirmed'),
                        'current_revision_id': task['current_event']['revision']['revision_id'] if task and task['current_event'] else None}
            return {'tasks': tasks, 'schedules': data['checks'], 'entries': data['entries'],
                    'calendar_metadata': metadata,
                    'history': data['audit'][-100:],
                    'plans': [{k: p[k] for k in ('id', 'target', 'created', 'outcome')} for p in data['plans'].values()],
                    'complete_schedule_verified': False}

    @app.post('/api/student/schedule')
    async def save_schedule(request: Request):
        schedule = await _body(request, workflow.Schedule)
        await run_in_threadpool(serialize_operation, request.state.student_session, workflow.save_schedule, schedule)
        return {'saved': True, 'independently_verified': False}

    @app.post('/api/student/schedules/batch')
    async def save_batch_schedule(request: Request):
        batch = await _body(request, workflow.BatchSchedule)
        return await run_in_threadpool(serialize_operation, request.state.student_session, workflow.save_batch, batch)

    @app.post('/api/student/calendar/preview')
    async def preview(request: Request):
        data = await _body(request, PreviewInput)
        if accounts.calendar_policy == 'registered-accept' and data.target != 'local':
            raise HTTPException(409, 'Strict FYP output uses the project-local sandbox only.')
        session = request.state.student_session
        remote = google_factory(session) if data.target == 'google' else None
        return await run_in_threadpool(serialize_operation, session, workflow.local_preview,
            list(data.task_ids), remote.target if remote else 'local', remote)

    @app.post('/api/student/calendar/approve')
    async def approve(request: Request):
        data = await _body(request, PlanInput)
        if not data.confirmed:
            raise HTTPException(422, 'Personally confirm the displayed changes first.')
        return await run_in_threadpool(serialize_operation, request.state.student_session, workflow.approve, data.plan_id)

    @app.post('/api/student/calendar/execute')
    async def execute(request: Request):
        data = await _body(request, PlanInput)
        session = request.state.student_session
        plan = workflow.get_plan(accounts, session, data.plan_id)
        remote = google_factory(session) if plan['target'].startswith('google:') else None
        return await run_in_threadpool(serialize_operation, session, workflow.execute, data.plan_id, remote)

    @app.post('/api/student/calendar/check')
    async def check(request: Request):
        data = await _body(request, PlanInput)
        session = request.state.student_session
        plan = workflow.get_plan(accounts, session, data.plan_id, active=False)
        if not plan['outcome']:
            raise HTTPException(409, 'This preview has not been applied.')
        if plan['target'] == 'local':
            with accounts.transaction() as state:
                entries = state['workspaces'][session.user_id]['entries']
                return {'status': 'SUCCEEDED' if all(entries.get(op['key']) == op['body'] for op in plan['operations'])
                        else 'CHECK_REQUIRED', 'read_only': True}
        return await run_in_threadpool(google_factory(session).reconcile, plan)

    @app.get('/api/student/export/{task_id}')
    def export(task_id: str, request: Request):
        session = request.state.student_session
        return serialize_operation(session, export_source, task_id)

    @app.post('/api/student/compare')
    async def compare(request: Request):
        data = await _body(request, CompareInput, max_bytes=2 * 1024 * 1024)
        return await run_in_threadpool(serialize_operation, request.state.student_session,
                                       compare_reference, data.task_id, data.reference)

    @app.post('/api/student/completeness')
    async def completeness(request: Request):
        data = await _body(request, CompletenessInput)
        if not all((data.original_pages_checked, data.groups_checked, data.no_missing_classes, data.exceptions_checked)):
            raise HTTPException(422, 'Complete every source checklist item first.')
        session = request.state.student_session
        rows = await run_in_threadpool(serialize_operation, session, workflow.collect, list(data.task_ids))
        # This is deliberately a personal checklist, never an independent attestation.
        with accounts.transaction(write=True) as state:
            workspace = state['workspaces'][session.user_id]
            workspace['personal_completeness'] = {'rows_digest': workflow.digest(rows), 'at': accounts.clock(),
                                                 'task_ids': list(data.task_ids)}
        return {'status': 'PERSONALLY_CHECKED', 'complete_schedule_verified': False,
                'message': 'Personal checklist saved. Independent verification is still required for verified learning windows.'}

    @app.post('/api/student/google/connect')
    def google_connect(request: Request):
        if accounts.calendar_policy == 'registered-accept':
            raise HTTPException(409, 'Strict FYP output uses the local sandbox. Google is an optional application extension.')
        invalidate_google_plans(request.state.student_session)
        return {'url': google_factory(request.state.student_session).start()}

    @app.get('/api/student/google/callback')
    def google_callback(request: Request):
        google = google_factory(request.state.student_session)
        try:
            google.callback(request.query_params.get('state', ''), request.query_params.get('code', ''))
            return RedirectResponse('/?google=connected', status_code=303)
        except ReviewError:
            return RedirectResponse('/?google=failed', status_code=303)

    @app.post('/api/student/google/disconnect')
    def google_disconnect(request: Request):
        request.state.student_session.google.clear()
        request.state.student_session.oauth.clear()
        invalidate_google_plans(request.state.student_session)
        return {'connected': False}

    def invalidate_google_plans(session):
        with accounts.transaction(write=True) as state:
            workspace = state['workspaces'][session.user_id]
            workspace['plans'] = {key: plan for key, plan in workspace['plans'].items()
                if not plan['target'].startswith('google:') or plan['outcome'] is not None}

    app.include_router(main.app.router)
    app.mount('/', StaticFiles(directory=ROOT / 'frontend'), name='student-assets')
    return app


def build_student_app(directory, *, origin='http://127.0.0.1:8020', registry=None,
                      calendar_policy='registered-accept', calendar_context=None, grounded_service=None, google_config=None):
    from backend.review.persistence import ReviewJournal
    from backend.review.windows_security import current_actor
    actor = current_actor()
    journal = ReviewJournal(directory, actor)
    factory = lambda name: ReviewJournal(journal.directory / name, actor)
    return create_student_app(Accounts(journal, factory, registry=registry,
                                      calendar_policy=calendar_policy, calendar_context=calendar_context),
                              origin=origin, grounded_service=grounded_service, google_config=google_config)
