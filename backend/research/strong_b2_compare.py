"""Capture B2-strong-v2 independently; references are opened only after inference."""
import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path
from dataclasses import asdict
from backend.baselines.strong_b2 import WordCollector,predict,CONFIG
from backend.services.file_extractor import extract_document
from backend.services.extraction_adapter import adapt_extraction,adapt_baseline
from backend.reconstruction.service import reconstruct,compare_modes
from backend.research.development_compare import save,score
from backend.research.diagnose_comparison import CORE


def contents(packet):
    return sorted(json.dumps(e['revision']['content'],sort_keys=True) for e in packet['events'])


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--previous-run',type=Path);p.add_argument('--full-application',type=Path)
    p.add_argument('--references',type=Path)
    a=p.parse_args(argv)
    if a.out.exists():raise ValueError('Use new output directory')
    collector=WordCollector()
    raw=extract_document(a.source.name,None,a.source.read_bytes(),force_ocr=True,collector=collector)
    packet=adapt_extraction(raw);strong=predict(packet.source,collector)
    result={'scope':'DEVELOPMENT_ONLY','source_sha256':raw.checksum,'configuration':CONFIG,
        'source_code_sha256':hashlib.sha256(Path(__file__).parents[1].joinpath('baselines/strong_b2.py').read_bytes()).hexdigest(),
        'python':platform.python_version(),'git_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'raw_evidence':raw.model_dump(mode='json'),
        'word_pages':[{**page,'words':[asdict(w) for w in page['words']]} for page in collector],
        'conditions':{'B2-strong-v2':{'packet':strong.model_dump(mode='json')}},
        'note':'Strong B2 uses original full-page words and observed colored boundaries; no P1 decoder, RT4 correction or reference input. Full STAIR uses different local OCR.'}
    for name in ['B1','B2','B2H-v1']:
        result['conditions'][name]={'packet':adapt_baseline(raw,name).model_dump(mode='json')}
    result['conditions']['Generic-P1']=reconstruct(packet,'constrained')
    modes=compare_modes(packet)
    result['matched_constraint_readiness']={'matching_checks_passed':modes['matching_checks_passed'],
        'registered_comparator_ready':False,'scope':modes['comparison_scope']}
    if a.previous_run:
        old=json.loads(a.previous_run.read_text(encoding='utf-8-sig'))
        if old['source_sha256']!=raw.checksum:raise ValueError('Old source mismatch')
        result['unchanged_conditions_replay']={}
        for name,new in result['conditions'].items():
            lookup='P1' if name=='Generic-P1' else name
            if lookup not in old['runs']:continue
            previous=old['runs'][lookup]
            oldp=previous.get('packet') or previous.get('canonical_output',{}).get('packet')
            newp=new.get('packet') or new.get('canonical_output',{}).get('packet')
            result['unchanged_conditions_replay'][name]=contents(oldp)==contents(newp)
    if a.full_application:
        full=json.loads(a.full_application.read_text(encoding='utf-8-sig'))
        if full['extraction']['source']['content_sha256']!=raw.checksum:raise ValueError('Application source mismatch')
        result['conditions']['STAIR-S2b-RT4']=full['reconstruction']
    result['counts']={}
    for name,r in result['conditions'].items():
        co=r.get('canonical_output') or {};pkt=r.get('packet') or co.get('packet')
        result['counts'][name]={'selected':len(pkt['events']) if pkt else None,
            'unresolved_projections':len(co.get('unresolved_candidates',[])),
            'issues':[x['code'] for x in pkt['issues']] if pkt else ['EXECUTION_ABORTED']}
    if a.references:
        ref=json.loads(a.references.read_text(encoding='utf-8-sig'))
        if ref['scope']!='DEVELOPMENT_ONLY' or ref['source_sha256']!=raw.checksum or not ref['checked_by'] or not ref['checked_at'] or ref['all_visible_sessions_checked'] is not True:raise ValueError('Manual development reference required')
        if {x['page'] for x in ref['pages']}!={x['page'] for x in collector}:raise ValueError('Reference page mismatch')
        result['metrics']={}
        for name,r in result['conditions'].items():
            pkt=r.get('packet') or (r.get('canonical_output') or {}).get('packet')
            if pkt is None:continue
            spans={s['evidence_id']:s['page'] for s in pkt['evidence']}
            scores={}
            for metric,fields in [('core',CORE),('core_venue',CORE+('location',)),('strict_declared',ref['fields'])]:
                tp=fp=fn=dup=0
                for pg in ref['pages']:
                    predictions=[]
                    for e in pkt['events']:
                        owner={spans[i] for o in e['revision']['observations'] for i in o['evidence_ids']}
                        if len(owner)!=1:raise ValueError('Unresolved event page')
                        if owner=={pg['page']}:predictions.append(e['revision']['content'])
                    if any(not set(fields)<=set(e) for e in pg['events']):raise ValueError('Missing reference fields')
                    s=score(predictions,pg['events'],fields);tp+=s['tp'];fp+=s['fp'];fn+=s['fn'];dup+=s['duplicates']
                scores[metric]={'tp':tp,'fp':fp,'fn':fn,'duplicates':dup,'precision':tp/(tp+fp) if tp+fp else None,
                    'recall':tp/(tp+fn) if tp+fn else None,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}
            result['metrics'][name]=scores
        result['reference_sha256']=hashlib.sha256(a.references.read_bytes()).hexdigest()
        result['reference_note']='User-attested development checking; not authenticated independent Gold. Null and empty weeks remain distinct.'
    a.out.mkdir(parents=True);save(a.out/'run.json',result)
    print(json.dumps({'counts':result['counts'],'metrics':result.get('metrics'),
        'unchanged_replay':result.get('unchanged_conditions_replay'),'matched':result['matched_constraint_readiness']}))


if __name__=='__main__':main()
