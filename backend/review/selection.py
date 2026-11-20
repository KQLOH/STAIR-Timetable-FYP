"""Chapter 3 Table 3.9 PRE-02/PRE-03 software, no label generation."""
from datetime import datetime,timezone
from fractions import Fraction
from typing import Literal
from math import isfinite
from pydantic import Field,model_validator
from backend.models.evidence import ContractModel
from backend.reconstruction.models import digest
from backend.review.operating_point import ValidationInput,ValidationRow,select_operating_point

TAU_GRID=tuple(x/100 for x in range(50,100,5))
REVIEW_FLOOR=.40
REQUIRED_ARTIFACTS={'code','evidence','candidates','rules','scoring','routing','calendar','baselines','annotations','splits','provider','privacy','analysis','seeds'}

class FrozenConfiguration(ContractModel):
    profile: str=Field(min_length=1)
    provider_id: str=Field(min_length=1)
    settings_digest: str=Field(pattern=r'^[0-9a-f]{64}$')
    artifacts: dict[str,str]
    parameters: dict[str,float]
    parameter_order: tuple[str,...]
    weights: tuple[int,int,int]
    penalty_weight: float=Field(ge=0,allow_inf_nan=False)
    required_accept_fields: tuple[str,...]
    review_floor: Literal[.40]=.40
    tau_grid: tuple[float,...]=TAU_GRID
    @model_validator(mode='after')
    def exact_protocol(self):
        from backend.models.event import EventContent
        from backend.research.workflow import REGISTERED_CONDITIONAL_FIELDS
        if self.tau_grid!=TAU_GRID or sum(self.weights)!=10000 or any(x<0 or x%500 for x in self.weights):raise ValueError('Frozen Chapter 3 grid/weights required')
        if set(self.parameter_order)!=set(self.parameters) or len(set(self.parameter_order))!=len(self.parameter_order):raise ValueError('Complete numeric parameter tuple required')
        if not all(isfinite(v) for v in self.parameters.values()):raise ValueError('Finite frozen parameters required')
        expected=dict(zip(('wX','wR','wC'),(v/10000 for v in self.weights)))
        expected['wP']=self.penalty_weight
        if any(self.parameters.get(k)!=v for k,v in expected.items()):raise ValueError('The full parameter tuple must bind all routing weights')
        if not REQUIRED_ARTIFACTS<=self.artifacts.keys() or any(len(v)!=64 or any(c not in '0123456789abcdef' for c in v) for v in self.artifacts.values()):raise ValueError('Complete freeze hashes required')
        if set(self.required_accept_fields)-set(EventContent.model_fields):raise ValueError('Unknown applicability field')
        if not {'start_time','end_time','recurrence','source_timezone'}<=set(self.required_accept_fields):raise ValueError('Mandatory temporal evidence cannot be removed')
        if not REGISTERED_CONDITIONAL_FIELDS<=set(self.required_accept_fields):raise ValueError('Registered conditional fields cannot be removed')
        return self
    def checksum(self):return digest(self.model_dump(mode='json'))
    def pipeline_digest(self):return digest({'profile':self.profile,'provider_id':self.provider_id,'artifacts':self.artifacts})
    def parameter_tuple(self):return tuple(self.parameters[k] for k in self.parameter_order)

class DevelopmentRow(ValidationRow):
    predicted: bool=True
    matched_reference_id: str|None=None
    review_eligible: bool
    unresolved_soft_issue: bool=False
    harmful_final_state: bool=False

class DevelopmentCandidate(ContractModel):
    configuration: FrozenConfiguration
    reference_count: int=Field(ge=0)
    rows: tuple[DevelopmentRow,...]
    @model_validator(mode='after')
    def one_to_one(self):
        ids=[r.matched_reference_id for r in self.rows if r.exact_event_correct and r.predicted]
        if len(ids)!=len(set(ids)) or any(i is None for i in ids) or len(ids)>self.reference_count:raise ValueError('One-to-one checked reference matches required')
        if len({r.ledger_id for r in self.rows})!=len(self.rows):raise ValueError('Duplicate ledger ID')
        return self

class DevelopmentInput(ContractModel):
    origin: Literal['SYNTHETIC','AUTHENTIC']
    partition: Literal['development']
    pre01_artifact_digest: str=Field(pattern=r'^[0-9a-f]{64}$')
    candidates: tuple[DevelopmentCandidate,...]=Field(min_length=1)
    @model_validator(mode='after')
    def common_contract(self):
        c=self.candidates
        if len({x.configuration.checksum() for x in c})!=len(c):raise ValueError('Duplicate configuration')
        if len({x.configuration.parameter_tuple() for x in c})!=len(c):raise ValueError('Non-threshold tuples must distinguish configurations')
        if len({x.configuration.parameter_order for x in c})!=1 or len({x.reference_count for x in c})!=1:raise ValueError('Common field order/reference frame required')
        frames=[{(r.source_sha256,r.source_page_id) for r in x.rows} for x in c]
        if any(frame!=frames[0] for frame in frames):raise ValueError('Every configuration must cover the same development frame')
        return self

def select_development(data:DevelopmentInput):
    vector=[]
    for candidate in data.candidates:
        tp=sum(r.predicted and r.exact_event_correct for r in candidate.rows)
        fp=sum(r.predicted and not r.exact_event_correct for r in candidate.rows)
        fn=candidate.reference_count-tp
        f1=Fraction(2*tp,2*tp+fp+fn) if 2*tp+fp+fn else Fraction(0)
        recall=Fraction(tp,tp+fn) if tp+fn else Fraction(0)
        points=[]
        for tau in TAU_GRID:
            accepted=[r for r in candidate.rows if r.predicted and r.gate_passed and not r.unresolved_soft_issue and r.score is not None and r.score>=tau]
            wrong=sum(not r.exact_event_correct for r in accepted)
            hard=sum(r.hard_violation for r in accepted)
            review=sum(r.predicted and r.gate_passed and not r.hard_violation and r not in accepted and r.review_eligible and r.score is not None and r.score>=REVIEW_FLOOR for r in candidate.rows)
            harmful=sum(r.harmful_final_state for r in candidate.rows)
            points.append({'tau':tau,'accepted':len(accepted),'incorrect':wrong,'accepted_hard_violations':hard,'harmful_final_states':harmful,'review':review,
                'feasible':bool(accepted and hard==0 and harmful==0 and wrong*20<=len(accepted))})
        feasible=[p for p in points if p['feasible']]
        vector.append({'configuration_digest':candidate.configuration.checksum(),'pipeline_digest':candidate.configuration.pipeline_digest(),'parameter_tuple':list(candidate.configuration.parameter_tuple()),
            'tp':tp,'fp':fp,'fn':fn,'f1':float(f1),'recall':float(recall),'points':points,'feasible':bool(feasible),
            'best_review_count':min((p['review'] for p in feasible),default=None)})
    eligible=[v for v in vector if v['feasible']]
    def order(v):
        tp,fp,fn=v['tp'],v['fp'],v['fn']
        return (-Fraction(2*tp,2*tp+fp+fn),-Fraction(tp,tp+fn),v['best_review_count'],tuple(v['parameter_tuple']))
    winner=min(eligible,key=order) if eligible else None
    result={'version':'pre02-v1','origin':data.origin,'status':'SELECTED_NON_THRESHOLD_CONFIGURATION' if winner else 'DEVELOPMENT_INFEASIBLE',
        'input_digest':digest(data.model_dump(mode='json')),'pre01_artifact_digest':data.pre01_artifact_digest,
        'selected_configuration_digest':winner['configuration_digest'] if winner else None,'candidate_vector':vector,
        'tau_grid':list(TAU_GRID),'development_tau_discarded':True,'recorded_at':datetime.now(timezone.utc).isoformat()}
    return {**result,'integrity_digest':digest(result)}

def verify_development(record,data):
    body={k:v for k,v in record.items() if k!='integrity_digest'}
    if record.get('integrity_digest')!=digest(body):raise ValueError('PRE-02 integrity mismatch')
    expected=select_development(data)
    if {k:v for k,v in body.items() if k!='recorded_at'}!={k:v for k,v in expected.items() if k not in {'recorded_at','integrity_digest'}}:raise ValueError('PRE-02 selection mismatch')
    stamp=datetime.fromisoformat(body['recorded_at'])
    if stamp.tzinfo is None or stamp>datetime.now(timezone.utc):raise ValueError('Actual dated freeze required')
    if body['status']!='SELECTED_NON_THRESHOLD_CONFIGURATION':raise ValueError('No feasible configuration')
    return next(c.configuration for c in data.candidates if c.configuration.checksum()==body['selected_configuration_digest'])

def select_validation(data:ValidationInput,pre02,development:DevelopmentInput):
    config=verify_development(pre02,development)
    if data.tau_grid!=TAU_GRID or data.pipeline_digest!=config.pipeline_digest() or data.scoring_configuration_digest!=config.checksum():raise ValueError('PRE-03 cannot change the frozen PRE-02 configuration/grid')
    if data.pre01_artifact_digest!=development.pre01_artifact_digest or data.pre02_artifact_digest!=pre02['integrity_digest']:raise ValueError('Preparation chain mismatch')
    if (development.origin=='SYNTHETIC')!=(data.origin=='SYNTHETIC'):raise ValueError('Evidence origins cannot be relabelled')
    result=select_operating_point(data)
    result['recorded_at']=datetime.now(timezone.utc).isoformat()
    result['integrity_digest']=digest(result)
    return result

def verify_validation(record,data,pre02,development):
    body={k:v for k,v in record.items() if k!='integrity_digest'}
    if record.get('integrity_digest')!=digest(body):raise ValueError('PRE-03 integrity mismatch')
    expected=select_validation(data,pre02,development)
    if {k:v for k,v in body.items() if k!='recorded_at'}!={k:v for k,v in expected.items() if k not in {'recorded_at','integrity_digest'}}:raise ValueError('PRE-03 threshold/vector mismatch')
    if body['selected_tau'] is None:raise ValueError('No feasible validation operating point')
    stamp=datetime.fromisoformat(body['recorded_at'])
    if stamp.tzinfo is None or stamp<datetime.fromisoformat(pre02['recorded_at']):raise ValueError('Freeze chronology mismatch')
    return verify_development(pre02,development),body['selected_tau']


def main():
    """Create-only offline selection. These files alone never grant ACCEPT."""
    import argparse,json
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('pre02','pre03'))
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--development',type=Path)
    parser.add_argument('--pre02',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.stage=='pre02':
            data=DevelopmentInput.model_validate_json(args.input.read_text(encoding='utf-8'))
            record=select_development(data)
        else:
            if not args.development or not args.pre02:raise ValueError('Frozen PRE-02 input required')
            data=ValidationInput.model_validate_json(args.input.read_text(encoding='utf-8'))
            development=DevelopmentInput.model_validate_json(args.development.read_text(encoding='utf-8'))
            record=select_validation(data,json.loads(args.pre02.read_text(encoding='utf-8')),development)
        # Authentic evidence and selection artefacts must stay outside public Git.
        root=Path(__file__).resolve().parents[2]
        if data.origin!='SYNTHETIC' and args.out.resolve().is_relative_to(root):raise ValueError('Private output required')
        text=json.dumps(record,indent=2,allow_nan=False)+'\n'
        with args.out.open('x',encoding='utf-8') as stream:stream.write(text)
        print(json.dumps({'status':record.get('status',record.get('selection_status')),'registered_accept_eligible':False}))
        return 0
    except (ValueError,OSError,KeyError,TypeError):
        print(json.dumps({'status':'REJECTED_INVALID_OR_EXISTING_SELECTION','registered_accept_eligible':False}))
        return 1


if __name__=='__main__':raise SystemExit(main())
