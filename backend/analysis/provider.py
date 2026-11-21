"""Explicitly approved compatible chat endpoint; no default provider or retries."""
import json
import os
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import AwareDatetime, Field, model_validator
from backend.models.evidence import ContractModel
from backend.analysis.grounded import PROMPT, PROMPT_DIGEST


class EvidenceFile(ContractModel):
    path: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ProviderSettings(ContractModel):
    schema_version: Literal['approved-operational-provider-v1']
    provider: str = Field(min_length=1, max_length=100)
    model_revision: str = Field(min_length=1, max_length=200)
    region: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(max_length=500)
    prompt_digest: Literal[PROMPT_DIGEST]
    approved_by: str = Field(min_length=1, max_length=100)
    approved_at: AwareDatetime
    approval_record_id: str = Field(min_length=1, max_length=200)
    privacy_record: EvidenceFile
    api_policy_record: EvidenceFile
    deletion_record: EvidenceFile
    training_excluded: Literal[True]
    retention_days: int = Field(ge=0, le=30)
    account_setting_record: str = Field(min_length=1, max_length=200)
    access_controls: str = Field(min_length=1, max_length=500)
    subprocessors: tuple[str, ...] = Field(min_length=1, max_length=30)
    temperature: Literal[0] = 0
    max_output_tokens: Literal[700] = 700
    timeout_seconds: Literal[30] = 30
    retries: Literal[0] = 0

    @model_validator(mode='after')
    def safe_endpoint(self):
        u = urlsplit(self.endpoint)
        if (u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment):
            raise ValueError('Explicit HTTPS endpoint without embedded credentials required')
        if self.approved_at > datetime.now(self.approved_at.tzinfo):
            raise ValueError('Future approval is invalid')
        return self


class CompatibleProvider:
    def __init__(self, path, approval_sha256, *, transport=None):
        self.path = Path(path).resolve()
        raw = self.path.read_bytes()
        if len(raw) > 65536 or sha256(raw).hexdigest() != approval_sha256:
            raise ValueError('Explicit provider approval hash mismatch')
        self.approval_sha256 = approval_sha256
        self.settings = ProviderSettings.model_validate_json(raw)
        self.transport = transport
        self._check_files()

    def _check_files(self):
        if sha256(self.path.read_bytes()).hexdigest() != self.approval_sha256:
            raise ValueError('Provider configuration changed')
        for item in (self.settings.privacy_record, self.settings.api_policy_record, self.settings.deletion_record):
            path = (self.path.parent/item.path).resolve()
            if not path.is_relative_to(self.path.parent) or sha256(path.read_bytes()).hexdigest() != item.sha256:
                raise ValueError('Provider/privacy evidence missing or changed')

    def ready(self):
        try:
            self._check_files()
            return bool(os.environ.get('STAIR_ANALYSIS_API_KEY'))
        except (OSError, ValueError): return False

    async def complete(self, payload):
        if not self.ready(): raise ValueError('Provider not ready')
        body = {'model': self.settings.model_revision, 'temperature': 0, 'max_completion_tokens': 700, 'store': False,
                'reasoning_effort': 'none',
                'response_format': {'type': 'json_object'}, 'messages': [
                    {'role': 'system', 'content': PROMPT},
                    {'role': 'user', 'content': json.dumps(payload, ensure_ascii=True)}]}
        transport = self.transport or httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(transport=transport, timeout=30, follow_redirects=False, trust_env=False) as client:
            async with client.stream('POST', self.settings.endpoint, json=body,
                headers={'Authorization': 'Bearer '+os.environ['STAIR_ANALYSIS_API_KEY']}) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536: raise ValueError('Provider response exceeds limit')
        data = json.loads(raw)
        if data['choices'][0].get('finish_reason') != 'stop': raise ValueError('Incomplete provider response')
        return json.loads(data['choices'][0]['message']['content'])
