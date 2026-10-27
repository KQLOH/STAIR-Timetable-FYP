"""Approved S4B demo-only channel. Not installed by normal application startup."""
from dataclasses import dataclass,field
from hashlib import sha256
from hmac import compare_digest
from http.cookies import SimpleCookie,CookieError
from ipaddress import ip_address
import json,re,secrets,time
from threading import RLock
from urllib.parse import urlsplit,parse_qs
from starlette.responses import JSONResponse

ORIGIN='http://127.0.0.1:8017'
HOST='127.0.0.1:8017'
PREFIX={'review':'/api/review','calendar':'/api/calendar/v1/test-only'}
COOKIE={'review':'stair_s4b_review','calendar':'stair_s4b_calendar'}
UUID=r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
ROUTES={
 ('review','GET'):re.compile(r'/api/review/(?:session|queue|tasks/'+UUID+r'(?:/(?:snapshot|history))?)\Z'),
 ('review','POST'):re.compile(r'/api/review/(?:ingest|session/end|tasks/'+UUID+r'/(?:transition|corrections|revalidate))\Z'),
 ('calendar','GET'):re.compile(r'/api/calendar/v1/test-only/(?:session|history|state)\Z'),
 ('calendar','POST'):re.compile(r'/api/calendar/v1/test-only/(?:session/end|preview|approve|execute|reconcile|revoke-approval)\Z')}

class Denied(Exception):
    """Intentionally contains no request, secret or remote response text."""


@dataclass(repr=False)
class Capability:
    authority:object
    principal:object
    bearer:str=field(repr=False)


@dataclass(repr=False)
class Pairing:
    workflow:str
    cookie_digest:str
    binding_digest:str
    nonce_digest:str
    pending_until:float
    paired:bool=False
    invalid:bool=False


def hashed(value):return sha256(value.encode('ascii')).hexdigest()


class Broker:
    def __init__(self,capabilities,*,clock=time.time,audit=None):
        if set(capabilities)!={'review','calendar'}:raise ValueError('Two separate approved capabilities required')
        if capabilities['review'].authority is capabilities['calendar'].authority:raise ValueError('Separate issuers required')
        self.capabilities=dict(capabilities);self.clock=clock;self.audit=audit or (lambda event:None)
        self._records={};self._nonces={};self._lock=RLock()

    def __repr__(self):return '<S4B Broker: credentials redacted>'

    def _valid_grant(self,workflow):
        cap=self.capabilities.get(workflow)
        if cap is None:raise Denied()
        try:cap.authority.require(cap.principal,'read')
        except Exception:raise Denied() from None
        return cap

    def pending(self,workflow):
        with self._lock:
            self._valid_grant(workflow)
            for key,record in list(self._records.items()):
                if record.invalid or (not record.paired and record.pending_until<=self.clock()):
                    self._records.pop(key,None);self._nonces.pop(record.nonce_digest,None)
            if sum(not r.paired for r in self._records.values())>=16:raise Denied()
            cookie=secrets.token_urlsafe(32);binding=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(18)
            record=Pairing(workflow,hashed(cookie),hashed(binding),hashed(nonce),self.clock()+120)
            self._records[record.cookie_digest]=record;self._nonces[record.nonce_digest]=record
            self.audit({'action':'PENDING_REQUEST','workflow':workflow})
            return cookie,{'pairing_nonce':nonce,'transport_binding':binding,'expires_in':120,'authorized':False}

    def confirm_from_console(self,workflow,nonce):
        """Only the trusted stdin loop calls this in a live host. No HTTP route."""
        with self._lock:
            self._valid_grant(workflow)
            if not isinstance(nonce,str) or not re.fullmatch(r'[A-Za-z0-9_-]{24}',nonce):raise Denied()
            record=self._nonces.get(hashed(nonce))
            if not record or record.workflow!=workflow or record.invalid or record.paired or record.pending_until<=self.clock():raise Denied()
            for other in self._records.values():
                if other.workflow==workflow and other.paired:other.invalid=True;other.paired=False
            record.paired=True;self._nonces.pop(record.nonce_digest,None)
            self.audit({'action':'MANUAL_CONSOLE_PAIR','workflow':workflow})

    def authenticate(self,workflow,cookie,binding):
        with self._lock:
            cap=self._valid_grant(workflow)
            if not all(isinstance(s,str) and re.fullmatch(r'[A-Za-z0-9_-]{43}',s) for s in (cookie,binding)):raise Denied()
            record=self._records.get(hashed(cookie))
            if not record or record.workflow!=workflow or not record.paired or record.invalid:raise Denied()
            if not compare_digest(record.binding_digest,hashed(binding)):raise Denied()
            return cap

    def invalidate(self,workflow):
        with self._lock:
            for record in self._records.values():
                if record.workflow==workflow:record.invalid=True;record.paired=False
            self.audit({'action':'CHANNEL_CLEARED','workflow':workflow})

    def revoke_all(self):
        with self._lock:
            for workflow,cap in self.capabilities.items():
                cap.authority.revoke(cap.principal);cap.bearer='';self.invalidate(workflow)
            self._nonces.clear();self._records.clear()
            self.audit({'action':'ALL_GRANTS_REVOKED'})


def allowed_route(workflow,method,path,query=''):
    expression=ROUTES.get((workflow,method))
    if expression is None or not expression.fullmatch(path):return False
    if query:
        if workflow!='review' or method!='GET' or path!='/api/review/queue':return False
        args=parse_qs(query,keep_blank_values=True)
        if not args or set(args)-{'offset','limit'} or any(len(v)!=1 or not v[0].isdigit() for v in args.values()):return False
    return True


async def small_body(receive,limit):
    data=bytearray()
    while True:
        message=await receive()
        if message['type']!='http.request':raise Denied()
        data.extend(message.get('body',b''))
        if len(data)>limit:raise Denied()
        if not message.get('more_body'):return bytes(data)


class TransportMiddleware:
    def __init__(self,app,broker,*,approved_pdf_sha=None):
        self.app=app;self.broker=broker;self.approved_pdf_sha=approved_pdf_sha

    @staticmethod
    def headers(scope):
        critical={b'host',b'origin',b'referer',b'cookie',b'authorization',b'sec-fetch-site',b'sec-fetch-mode',b'sec-fetch-dest',b'x-stair-demo-transport',b'x-stair-demo-binding'}
        pairs=scope.get('headers',[])
        if any(sum(k.lower()==name for k,_ in pairs)>1 for name in critical):raise Denied()
        if any(k.lower().startswith(b'x-forwarded-') or k.lower() in {b'forwarded',b'x-real-ip'} for k,_ in pairs):raise Denied()
        h={k.lower().decode('latin1'):v.decode('latin1') for k,v in pairs}
        peer=(scope.get('client') or ('',))[0]
        if peer!='127.0.0.1' or h.get('host')!=HOST:raise Denied()
        return h

    @staticmethod
    def browser_boundary(h,method):
        if h.get('sec-fetch-site')!='same-origin' or h.get('sec-fetch-mode') not in {'cors','same-origin'} or h.get('sec-fetch-dest')!='empty':raise Denied()
        if 'origin' in h:
            if h['origin']!=ORIGIN:raise Denied()
        elif method=='GET':
            ref=urlsplit(h.get('referer',''))
            if ref.scheme!='http' or ref.netloc!=HOST or ref.username or ref.password:raise Denied()
        else:raise Denied()

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        try:
            h=self.headers(scope);path=scope['path'];method=scope['method']
            raw=scope.get('raw_path',path.encode())
            if b'%' in raw or '\\' in path or '//' in path:raise Denied()
            query=scope.get('query_string',b'').decode('ascii')
            if path.startswith('/api/intranet/'):raise Denied()
            if path.startswith('/__s4b_host__/'):
                match=re.fullmatch(r'/__s4b_host__/pending/(review|calendar)',path)
                if not match:return await JSONResponse({'detail':'NOT_FOUND'},404)(scope,receive,send)
                self.browser_boundary(h,method)
                if method!='POST' or query or 'authorization' in h or h.get('content-type','').split(';')[0]!='application/json':raise Denied()
                if json.loads(await small_body(receive,1024))!={}:raise Denied()
                workflow=match[1];cookie,payload=self.broker.pending(workflow)
                response=JSONResponse(payload,headers={'Cache-Control':'no-store','Pragma':'no-cache','Referrer-Policy':'same-origin'})
                response.set_cookie(COOKIE[workflow],cookie,path=PREFIX[workflow],httponly=True,samesite='strict',secure=False)
                return await response(scope,receive,send)
            workflow=next((key for key,prefix in PREFIX.items() if path==prefix or path.startswith(prefix+'/')),None)
            if workflow:
                self.browser_boundary(h,method)
                if not allowed_route(workflow,method,path,query) or 'authorization' in h or h.get('x-stair-demo-transport')!=workflow:raise Denied()
                name=COOKIE[workflow];cookie_header=h.get('cookie','')
                if sum(item.strip().partition('=')[0]==name for item in cookie_header.split(';'))!=1:raise Denied()
                parsed=SimpleCookie();parsed.load(cookie_header)
                value=parsed[name].value if name in parsed else ''
                cap=self.broker.authenticate(workflow,value,h.get('x-stair-demo-binding',''))
                forwarded=dict(scope);forwarded['headers']=[(k,v) for k,v in scope['headers'] if k.lower() not in {b'cookie',b'x-stair-demo-binding',b'x-stair-demo-transport'}]+[(b'authorization',('Bearer '+cap.bearer).encode())]
                async def safe_send(message):
                    if message['type']=='http.response.start':
                        message=dict(message);headers=[(k,v) for k,v in message.get('headers',[]) if k.lower() not in {b'cache-control',b'pragma'}]
                        headers.extend([(b'cache-control',b'no-store'),(b'pragma',b'no-cache')])
                        if path==PREFIX[workflow]+'/session/end' and 200<=message['status']<300:
                            self.broker.invalidate(workflow)
                            response=JSONResponse({});response.delete_cookie(COOKIE[workflow],path=PREFIX[workflow],httponly=True,samesite='strict')
                            headers.extend((k,v) for k,v in response.raw_headers if k==b'set-cookie')
                        message['headers']=headers
                    await send(message)
                return await self.app(forwarded,receive,safe_send)
            if path=='/api/extract' and self.approved_pdf_sha:
                if method!='POST' or h.get('x-filename')!='S4B-G01-review.pdf' or h.get('content-type','').split(';')[0]!='application/pdf':raise Denied()
                content=await small_body(receive,1533)
                if len(content)!=1533 or sha256(content).hexdigest()!=self.approved_pdf_sha:raise Denied()
                consumed=False
                async def replay():
                    nonlocal consumed
                    if not consumed:consumed=True;return {'type':'http.request','body':content,'more_body':False}
                    return await receive()
                return await self.app(scope,replay,send)
            return await self.app(scope,receive,send)
        except (Denied,CookieError,UnicodeError,ValueError,KeyError):
            return await JSONResponse({'detail':'S4B_TRANSPORT_DENIED'},403,headers={'Cache-Control':'no-store','Pragma':'no-cache'})(scope,receive,send)
