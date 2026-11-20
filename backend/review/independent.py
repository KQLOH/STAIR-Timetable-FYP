"""Owner-consented, separate authenticated reviewer channels. No HTTP role minting."""
from dataclasses import dataclass,field
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
import secrets,time,base64
from threading import RLock
from backend.review.models import ReviewError
from backend.review.windows_independent import authenticate_independent
from backend.review.auth import utc_now
from backend.analysis.schedule_checks import VerificationSubmission,CheckAnalysis

COOKIE='stair_independent_review'

@dataclass(repr=False)
class Invitation:
    check_id:str
    owner:object
    binding:str
    authors:set
    raw:bytes=field(repr=False)
    media:str=''
    expires:float=0

@dataclass(repr=False)
class Channel:
    invitation:Invitation
    cookie_hash:str
    binding_hash:str
    expires:float
    principal:object=None

class IndependentAccess:
    def __init__(self,host,checks,*,authenticate=authenticate_independent,clock=time.time):
        self.host,self.checks,self.authenticate_identity,self.clock=host,checks,authenticate,clock
        self.invites={};self.pending={};self.channels={};self.approvals={};self.lock=RLock()

    def _owner(self):
        if self.host.identity()!=self.host.actor:raise ReviewError('OWNER_LOGON_CHANGED',403)

    def invite_from_console(self,check_id,path):
        self._owner()
        owners=[c.principal for c in self.host.channels.values() if c.principal.actor_id==self.host.actor]
        if not owners:raise ReviewError('OWNER_SOURCE_AUTHORIZATION_REQUIRED',403)
        owner=owners[-1];check=self.checks.get(check_id,owner)
        if check['primary_actor']!=owner.actor_id or check['status'] not in {'AWAITING_INDEPENDENT_REVIEW','INDEPENDENTLY_CHECKED','INDEPENDENTLY_CHECKED_REAUTH_REQUIRED'}:raise ReviewError('PRIMARY_INVENTORY_INCOMPLETE',409)
        source=Path(path).resolve(strict=True)
        if not 0<source.stat().st_size<=20*1024*1024:raise ReviewError('SOURCE_PREVIEW_LIMIT',413)
        with source.open('rb') as stream:raw=stream.read(20*1024*1024+1)
        if len(raw)>20*1024*1024:raise ReviewError('SOURCE_PREVIEW_LIMIT',413)
        if sha256(raw).hexdigest()!=check['source']['content_sha256']:raise ReviewError('SOURCE_PREVIEW_HASH_OR_PAGE_MISMATCH',422)
        binding,authors=self.checks.invitation_binding(check_id,owner)
        with self.lock:
            self.prune()
            if len(self.invites)+len(self.channels)>=4:raise ReviewError('REVIEWER_CHANNEL_CAPACITY',409)
            code=secrets.token_urlsafe(32)
            self.invites[sha256(code.encode()).hexdigest()]=Invitation(check_id,owner,binding,authors,raw,check['source']['media_type'],self.clock()+600)
            return code

    def prune(self):
        now=self.clock()
        self.invites={k:v for k,v in self.invites.items() if v.expires>now}
        self.pending={k:v for k,v in self.pending.items() if v.expires>now}
        self.approvals={k:v for k,v in self.approvals.items() if v[3]>now}
        for key,record in list(self.channels.items()):
            if record.expires<=now:self.host.store.authority.revoke(record.principal);del self.channels[key]

    def request(self,code):
        with self.lock:
            self.prune();invite=self.invites.get(sha256(str(code).encode()).hexdigest())
            if not invite:raise ReviewError('REVIEWER_INVITATION_UNAVAILABLE',403)
            if len(self.pending)>=8:raise ReviewError('REVIEWER_CHANNEL_CAPACITY',409)
            cookie,binding,nonce=secrets.token_urlsafe(32),secrets.token_urlsafe(32),secrets.token_urlsafe(18)
            self.pending[nonce]=Channel(invite,sha256(cookie.encode()).hexdigest(),sha256(binding.encode()).hexdigest(),self.clock()+120)
            return cookie,{'nonce':nonce,'binding':binding,'authorized':False,'expires_in':120}

    def authenticate_from_console(self,nonce):
        self._owner()
        with self.lock:
            self.prune();record=self.pending.get(nonce)
            if not record:raise ReviewError('REVIEWER_INVITATION_UNAVAILABLE',403)
            original=record.invitation
            binding,authors=self.checks.invitation_binding(original.check_id,original.owner)
            if binding!=original.binding:raise ReviewError('STALE_REVIEWER_INVITATION',409)
        # Native prompt only in the owner's interactive console, never HTTP.
        actor=self.authenticate_identity(purpose="sign-in")
        with self.lock:
            self.prune()
            if self.pending.get(nonce) is not record:raise ReviewError('REVIEWER_INVITATION_UNAVAILABLE',403)
            binding,authors=self.checks.invitation_binding(original.check_id,original.owner)
            if binding!=original.binding:raise ReviewError('STALE_REVIEWER_INVITATION',409)
            if not isinstance(actor,str) or not actor.strip():raise ReviewError('WINDOWS_REVIEWER_AUTHENTICATION_REQUIRED',403)
            if actor==self.host.actor or actor in authors:raise ReviewError('INDEPENDENT_VERIFIER_REQUIRED',403)
            if self.host.grant_mode!='TEST_ONLY' and not actor.startswith('windows-logon:S-1-'):raise ReviewError('WINDOWS_REVIEWER_AUTHENTICATION_REQUIRED',403)
            source=self.checks.get(original.check_id,original.owner)['source']
            principal,_=self.host.store.authority.issue(actor,{'read','oversee'},source_bindings={(source['source_document_id'],source['content_sha256'])},mode=self.host.grant_mode,lifetime_minutes=15)
            try:self.checks.enrol_independent(principal,independent_of=tuple(authors|{self.host.actor}),expires_at=principal.expires_at)
            except Exception:self.host.store.authority.revoke(principal);raise
            record.principal=principal;record.expires=self.clock()+900
            self.channels[record.cookie_hash]=record;self.pending.pop(nonce)
            self.invites={k:v for k,v in self.invites.items() if v is not original}
            self.pending={k:v for k,v in self.pending.items() if v.invitation is not original}

    def authenticate(self,cookie,binding):
        with self.lock:
            self.prune();record=self.channels.get(sha256(str(cookie).encode()).hexdigest())
            if not record or not compare_digest(record.binding_hash,sha256(str(binding).encode()).hexdigest()):raise ReviewError('INDEPENDENT_REVIEW_AUTHENTICATION_REQUIRED',403)
            self.host.store.authority.require(record.principal,'read')
            actual,_=self.checks.invitation_binding(record.invitation.check_id,record.principal)
            if actual!=record.invitation.binding:
                self.host.store.authority.revoke(record.principal);self.channels.pop(record.cookie_hash,None)
                raise ReviewError('STALE_REVIEWER_INVITATION',409)
            return record

    def current(self,record):
        result=self.checks.reviewer_packet(record.invitation.check_id,record.principal)
        result['reviewer']={'authenticated':True,'distinct_identity':True,'synthetic':record.principal.mode=='TEST_ONLY','expires_at':record.principal.expires_at.isoformat()}
        return result

    def request_verification(self,record,body):
        with self.lock:
            self.prune()
            if len(self.approvals)>=8:raise ReviewError('REVIEWER_CHANNEL_CAPACITY',409)
            current=self.checks.get(record.invitation.check_id,record.principal)
            if current['version']!=body.expected_version:raise ReviewError('STALE_SCHEDULE_CHECK',409)
            if not all((body.source_confirmed,body.inventory_confirmed,body.temporal_context_confirmed)):raise ReviewError('INDEPENDENT_SOURCE_CONFIRMATION_REQUIRED',422)
            nonce=secrets.token_urlsafe(18)
            self.approvals[nonce]=(record,body,record.invitation.binding,self.clock()+120)
            return {'status':'WAITING_FOR_WINDOWS_CONFIRMATION','nonce':nonce,'expires_in':120}

    def confirm_from_console(self,nonce):
        self._owner()
        with self.lock:
            self.prune();pending=self.approvals.get(nonce)
            if not pending:raise ReviewError('REVIEWER_CONFIRMATION_UNAVAILABLE',403)
            record,body,binding,_=pending
            current,_=self.checks.invitation_binding(record.invitation.check_id,record.principal)
            if current!=binding:raise ReviewError('STALE_REVIEWER_INVITATION',409)
        check=self.checks.get(record.invitation.check_id,record.principal)
        label=check['scope']['start']+' to '+check['scope']['end']+'; source '+check['source']['content_sha256'][:12]
        actor=self.authenticate_identity(purpose='verification',context_label=label)
        with self.lock,self.checks.lock,self.host.store._lock:
            self.prune()
            if self.approvals.pop(nonce,None) is not pending:raise ReviewError('REVIEWER_CONFIRMATION_UNAVAILABLE',403)
            self.host.store.authority.require(record.principal,'read')
            current,_=self.checks.invitation_binding(record.invitation.check_id,record.principal)
            if actor!=record.principal.actor_id:raise ReviewError('SAME_AUTHENTICATED_REVIEWER_REQUIRED',403)
            if current!=binding:raise ReviewError('STALE_REVIEWER_INVITATION',409)
            result=self.checks.verify(record.invitation.check_id,body,record.principal)
            record.invitation.binding,_=self.checks.invitation_binding(record.invitation.check_id,record.principal)
            return result

    def source(self,record,page):
        invite=record.invitation
        if invite.media=='application/pdf':return {'kind':'image','media_type':'image/png',**self.checks.source_page(invite.check_id,page,invite.raw,record.principal)}
        if page!=1:raise ReviewError('SOURCE_PREVIEW_HASH_OR_PAGE_MISMATCH',422)
        if invite.media=='text/plain':return {'kind':'text','text':invite.raw.decode('utf-8-sig')}
        if invite.media not in {'image/png','image/jpeg'}:raise ReviewError('SOURCE_PREVIEW_UNAVAILABLE',422)
        return {'kind':'image','media_type':invite.media,'png_base64':base64.b64encode(invite.raw).decode('ascii')}

    def end(self,record):
        with self.lock:
            self.host.store.authority.revoke(record.principal);self.channels.pop(record.cookie_hash,None)
            self.checks.invalidate(record.invitation.check_id)
            self.approvals={k:v for k,v in self.approvals.items() if v[0] is not record}

    def revoke_from_console(self):
        self._owner()
        with self.lock:
            for record in self.channels.values():
                self.host.store.authority.revoke(record.principal);self.checks.invalidate(record.invitation.check_id)
            self.invites.clear();self.pending.clear();self.approvals.clear();self.channels.clear()
