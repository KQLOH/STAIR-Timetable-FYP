"""Operational grounded-provider orchestration; not the registered RQ4 experiment.

The provider selects structured facts/windows. Displayed wording is rendered
locally, so arbitrary provider prose cannot smuggle unsupported factual claims.
No source text, identity, credentials or course labels enter its input.
"""
import asyncio
from copy import deepcopy
from datetime import datetime
from typing import Literal

from pydantic import Field
from fastapi.concurrency import run_in_threadpool
from backend.models.evidence import ContractModel
from backend.analysis.service import analyze
from backend.reconstruction.models import digest

PROMPT = ('p1a_grounded_recommendation_v1: Return only JSON with claims, recommendations, no_window. '
          'claims is a list of {fact_id,value}, copied exactly from supplied facts. '
          'recommendations is a list of {window_id,activity}, where activity is STUDY or PROJECT. '
          'Use only supplied non-overlapping windows and fact IDs. '
          'Set no_window true exactly when allowed_windows is empty. Do not add prose or other keys.')
PROMPT_DIGEST = digest(PROMPT)


class Claim(ContractModel):
    fact_id: str = Field(max_length=20)
    value: object


class Suggestion(ContractModel):
    window_id: str = Field(max_length=20)
    activity: Literal['STUDY', 'PROJECT']


class ProviderOutput(ContractModel):
    claims: tuple[Claim, ...] = Field(max_length=30)
    recommendations: tuple[Suggestion, ...] = Field(max_length=12)
    no_window: bool


SAFE_FACTS = {'observed_scheduled_hours', 'normalized_session_count', 'hours_by_day',
              'day_event_distribution', 'tied_busiest_active_days', 'tied_lightest_active_days',
              'earliest_latest_observed', 'occupancy_intervals', 'total_scope_workload_hours'}


def provider_input(request, result):
    facts = [{'fact_id': 'F'+str(i), 'type': f['type'], 'value': deepcopy(f['value'])}
             for i, f in enumerate(result['facts']) if f['type'] in SAFE_FACTS and f['status'] == 'COMPUTED']
    windows = [{'window_id': 'W'+str(i), **r['window']} for i, r in enumerate(result['recommendations'])
               if r['type'] == 'OPTIONAL_STUDY_WINDOW'][:12]
    busy = next((f['value'] for f in result['facts'] if f['type'] == 'occupancy_intervals'), [])
    return {'prompt_key': 'p1a_grounded_recommendation_v1',
            'scope': {k: request.scope.model_dump(mode='json')[k] for k in ('start', 'end', 'timezone')},
            'facts': facts, 'busy_intervals': deepcopy(busy), 'allowed_windows': windows,
            'term_bounds': {'start': request.context.term.start.isoformat(), 'end': request.context.term.end.isoformat()}
                if request.context.term else None}


def validate_output(raw, payload):
    answer = ProviderOutput.model_validate(raw)
    facts = {f['fact_id']: f for f in payload['facts']}
    windows = {w['window_id']: w for w in payload['allowed_windows']}
    if answer.no_window != (not windows):
        raise ValueError('Unsupported no-window claim')
    ids = [c.fact_id for c in answer.claims]
    if len(set(ids)) != len(ids): raise ValueError('Duplicate claims')
    for claim in answer.claims:
        if claim.fact_id not in facts or digest(claim.value) != digest(facts[claim.fact_id]['value']):
            raise ValueError('Unsupported fact')
    chosen = [r.window_id for r in answer.recommendations]
    if len(set(chosen)) != len(chosen) or any(w not in windows for w in chosen):
        raise ValueError('Unsupported window')
    if windows and not chosen: raise ValueError('Feasible-window response omitted all recommendations')
    intervals = [(datetime.fromisoformat(windows[w]['start']), datetime.fromisoformat(windows[w]['end'])) for w in chosen]
    if any(max(a, c) < min(b, d) for i, (a, b) in enumerate(intervals) for c, d in intervals[:i]):
        raise ValueError('Overlapping recommendations')
    return {'claims': [facts[c.fact_id] for c in answer.claims],
            'recommendations': [{**windows[r.window_id], 'activity': r.activity,
                'explanation': ('Optional study time' if r.activity == 'STUDY' else 'Optional project time')+
                    ' within your declared availability and outside the checked classes and buffer.'}
                for r in answer.recommendations], 'no_window': answer.no_window}


class GroundedAnalyser:
    def __init__(self, provider=None): self.provider = provider

    async def run(self, request, authority=None, *, allow_provider=False):
        # Registered research inputs continue through their separate frozen protocol.
        if request.profile == 'registered': raise ValueError('REGISTERED_ANALYSIS_CONFIGURATION_NOT_READY')
        result = await run_in_threadpool(analyze, request, authority)
        # A fallback must be facts-only, not development recommendations disguised as C1.
        delivered = deepcopy(result)
        delivered['recommendations'] = []
        delivered['recommendation_status'] = 'FACTS_ONLY'
        delivered['grounded'] = {'status': 'NOT_EXECUTED', 'reason': None, 'external_calls': 0,
                                'research_status': 'OPERATIONAL_NOT_REGISTERED_P1A', 'output': None}
        state = delivered['grounded']
        if not result['completeness']['trusted_complete']:
            state['reason'] = 'INDEPENDENT_COMPLETE_SCHEDULE_REQUIRED'
            return delivered
        if result['window_status'] == 'NOT_ASSESSED':
            state['reason'] = 'EXPLICIT_VALID_AVAILABILITY_REQUIRED'
            return delivered
        if not allow_provider:
            state['reason'] = 'EXPLICIT_PROVIDER_CONSENT_REQUIRED'
            return delivered
        if self.provider is None or not self.provider.ready():
            state['reason'] = 'APPROVED_PROVIDER_CONFIGURATION_REQUIRED'
            return delivered
        payload = provider_input(request, result)
        state.update(status='FALLBACK', reason='PROVIDER_OR_POSTCHECK_FAILED', external_calls=1,
                     input_digest=digest(payload), prompt_digest=PROMPT_DIGEST)
        delivered['external_calls'] = True
        try:
            # One attempt, total deadline. Provider transport also has an I/O timeout.
            raw = await asyncio.wait_for(self.provider.complete(deepcopy(payload)), timeout=30)
            checked = validate_output(raw, payload)
            if authority is None or not authority.assess(request)[0]:
                fresh = await run_in_threadpool(analyze, request, authority)
                fresh['recommendations'] = []
                fresh['recommendation_status'] = 'FACTS_ONLY'
                state['reason'] = 'COMPLETENESS_REVOKED_DURING_GENERATION'
                fresh.update(grounded=state, external_calls=True)
                return fresh
            state.update(status='VALIDATED', reason=None, output=checked)
            delivered['recommendations'] = checked['recommendations']
            delivered['recommendation_status'] = 'VALIDATED_OPERATIONAL_GROUNDED_OUTPUT'
        except Exception:
            # Never return exception text: providers can echo secrets/source values.
            pass
        delivered['external_calls'] = True
        return delivered
