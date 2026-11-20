"""Offline PRE-03 selection/integrity checks, never an ACCEPT authority.

Chapter 3 Table 3.9: feasibility is nonzero ACCEPT, coverage >= .80 and
zero accepted hard violations; ties minimize risk, then maximize coverage,
then minimize tau. PRE-01/PRE-02 evidence and human approvals remain separate.
"""
from datetime import datetime, timezone
from fractions import Fraction
from typing import Literal
from pydantic import Field, model_validator
from backend.models.evidence import ContractModel
from backend.reconstruction.models import digest

class ValidationRow(ContractModel):
    ledger_id: str = Field(min_length=1)
    source_page_id: str | None = None
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    revision_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    reference_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    score: float | None = Field(default=None,ge=0,le=1,allow_inf_nan=False)
    gate_passed: bool
    exact_event_correct: bool
    hard_violation: bool


class ValidationInput(ContractModel):
    version: Literal['pre03-software-v1'] = 'pre03-software-v1'
    origin: Literal['SYNTHETIC','INDEPENDENT_VALIDATION_CLAIM']
    partition: Literal['validation']
    pipeline_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    scoring_configuration_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    pre01_artifact_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    pre02_artifact_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    tau_grid: tuple[float,...] = Field(min_length=1,max_length=10001)
    rows: tuple[ValidationRow,...]

    @model_validator(mode='after')
    def frozen_grid_and_ledger(self):
        if any(not 0 <= tau <= 1 for tau in self.tau_grid) or tuple(sorted(set(self.tau_grid))) != self.tau_grid:
            raise ValueError('A complete ordered unique frozen tau grid in [0,1] is required')
        if len({r.ledger_id for r in self.rows}) != len(self.rows):
            raise ValueError('The common ledger cannot contain duplicate row IDs')
        return self


def select_operating_point(data: ValidationInput):
    """Caller-supplied labels are evaluated, never certified as human checked."""
    vector = []
    for tau in data.tau_grid:
        accepted = [r for r in data.rows if r.gate_passed and r.score is not None and r.score >= tau]
        n, population = len(accepted), len(data.rows)
        wrong = sum(not r.exact_event_correct for r in accepted)
        hard = sum(r.hard_violation for r in accepted)
        feasible = bool(n and population and n*5 >= population*4 and hard == 0)
        vector.append({'tau':tau,'population':population,'accepted':n,'incorrect':wrong,
            'accepted_hard_violations':hard,'coverage':n/population if population else None,
            'selective_risk':wrong/n if n else None,'feasible':feasible})
    feasible = [p for p in vector if p['feasible']]
    best = min(feasible,key=lambda p:(Fraction(p['incorrect'],p['accepted']),-p['accepted'],p['tau'])) if feasible else None
    return {'version':data.version,'selection_status':'PROPOSED_NOT_REGISTERED' if best else 'VALIDATION_INFEASIBLE',
        'selected_tau':best['tau'] if best else None,'candidate_vector':vector,
        'input_digest':digest(data.model_dump(mode='json')),'pipeline_digest':data.pipeline_digest,
        'scoring_configuration_digest':data.scoring_configuration_digest,
        'pre01_artifact_digest':data.pre01_artifact_digest,'pre02_artifact_digest':data.pre02_artifact_digest,
        'origin':data.origin,'registered_accept_eligible':False,
        'authority':'CALLER_SUPPLIED_LABELS_NOT_INDEPENDENTLY_AUTHENTICATED',
        'score_interpretation':'FROZEN_RANKING_NOT_CALIBRATED_PROBABILITY'}


def freeze_selection(data: ValidationInput):
    record = {**select_operating_point(data),'recorded_at':datetime.now(timezone.utc).isoformat()}
    return {**record,'integrity_digest':digest(record)}


def load_selection(record, data: ValidationInput, *, pipeline_digest, scoring_configuration_digest):
    """Integrity-only loader: never grants runtime policy/calendar authority."""
    body = {k:v for k,v in record.items() if k != 'integrity_digest'}
    if record.get('integrity_digest') != digest(body):
        raise ValueError('Frozen selection integrity mismatch')
    if pipeline_digest != data.pipeline_digest or scoring_configuration_digest != data.scoring_configuration_digest:
        raise ValueError('Selection configuration mismatch')
    expected = select_operating_point(data)
    if {k:v for k,v in body.items() if k != 'recorded_at'} != expected:
        raise ValueError('Selection differs from the frozen input/grid or claims authority')
    stamp = datetime.fromisoformat(body['recorded_at'])
    if stamp.tzinfo is None:
        raise ValueError('Actual timezone-aware freeze timestamp required')
    return record


if __name__ == '__main__':
    import argparse,json
    from pathlib import Path
    cli=argparse.ArgumentParser(description='Offline PRE-03 proposal/integrity check; never grants ACCEPT')
    cli.add_argument('--input',type=Path,required=True)
    choice=cli.add_mutually_exclusive_group(required=True)
    choice.add_argument('--out',type=Path)
    choice.add_argument('--check',type=Path)
    args=cli.parse_args()
    try:
        data=ValidationInput.model_validate_json(args.input.read_text(encoding='utf-8'))
        if args.check:
            record=json.loads(args.check.read_text(encoding='utf-8'))
            load_selection(record,data,pipeline_digest=data.pipeline_digest,scoring_configuration_digest=data.scoring_configuration_digest)
        else:
            record=freeze_selection(data)
            with args.out.open('x',encoding='utf-8') as output:json.dump(record,output,indent=2,allow_nan=False)
        print(json.dumps({'selection_status':record['selection_status'],'registered_accept_eligible':False}))
    except (ValueError,OSError,KeyError,TypeError):
        print(json.dumps({'status':'REJECTED_INVALID_OR_EXISTING_ARTIFACT','registered_accept_eligible':False}))
        raise SystemExit(1) from None
