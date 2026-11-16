"""Score immutable development captures; never rerun OCR or edit references."""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from backend.models.event import EventContent
from backend.research.development_compare import FIELDS,score,save
from backend.research.diagnose_comparison import CORE


def evaluate(run,reference):
    if run['scope']!='DEVELOPMENT_ONLY' or reference['scope']!='DEVELOPMENT_ONLY':raise ValueError('Development only')
    if run['source_sha256']!=reference['source_sha256']:raise ValueError('Reference source mismatch')
    if not reference['checked_by'] or reference['all_visible_sessions_checked'] is not True:raise ValueError('Manual checking required')
    if datetime.fromisoformat(reference['checked_at']).tzinfo is None:raise ValueError('Timezone-aware checking time required')
    if len(reference['fields'])!=len(FIELDS) or set(reference['fields'])!=set(FIELDS):raise ValueError('Declared 13-field contract required')
    pages=reference['pages'];ids=[p['page'] for p in pages]
    if len(ids)!=len(set(ids)) or set(ids)!={p['page'] for p in run['raw_evidence']['pages']}:raise ValueError('Page population mismatch')
    for pg in pages:
        for e in pg['events']:
            if not set(FIELDS)<=set(e):raise ValueError('Missing reference fields')
            EventContent.model_validate(e)
    results={}
    for method,result in run['conditions'].items():
        packet=result.get('packet') or (result.get('canonical_output') or {}).get('packet')
        if packet is None:results[method]={'failure':True};continue
        spans={s['evidence_id']:s.get('page') for s in packet['evidence']}
        bypage={p:[] for p in ids}
        for e in packet['events']:
            owners={spans.get(i) for o in e['revision']['observations'] for i in o['evidence_ids']}
            if len(owners)!=1 or next(iter(owners)) not in bypage:raise ValueError('Prediction page ownership unresolved')
            bypage[next(iter(owners))].append(e['revision']['content'])
        metrics={}
        for name,fields in [('core',CORE),('core_venue',CORE+('location',)),('strict_declared',reference['fields'])]:
            counts={k:0 for k in ('tp','fp','fn','duplicates')}
            for pg in pages:
                s=score(bypage[pg['page']],pg['events'],fields)
                for k in counts:counts[k]+=s[k]
            tp,fp,fn=(counts[k] for k in ('tp','fp','fn'))
            metrics[name]={**counts,'precision':tp/(tp+fp) if tp+fp else None,
                'recall':tp/(tp+fn) if tp+fn else None,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}
        results[method]=metrics
    return {'scope':'DEVELOPMENT_ONLY_USER_ATTESTED_NOT_AUTHENTICATED_GOLD','results':results,
        'metric_fields':{'core':CORE,'core_venue':CORE+('location',),'strict_declared':reference['fields']},
        'preprocessing':'Strong B2: full-page OCR words and template geometry. Full STAIR: separate block OCR and RT4. End-to-end comparison, not constraint ablation.'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--references',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args(argv)
    report=evaluate(json.loads(a.run.read_text(encoding='utf-8-sig')),json.loads(a.references.read_text(encoding='utf-8-sig')))
    report.update(run_sha256=hashlib.sha256(a.run.read_bytes()).hexdigest(),reference_sha256=hashlib.sha256(a.references.read_bytes()).hexdigest())
    save(a.out,report);print(json.dumps(report))


if __name__=='__main__':main()
