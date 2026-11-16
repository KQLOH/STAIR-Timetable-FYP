"""Development-only shared-evidence comparison; no held-out or calendar execution."""
import argparse
import json
import unicodedata
from collections import Counter
from pathlib import Path

from backend.services.file_extractor import extract_document
from backend.services.extraction_adapter import adapt_extraction, adapt_baseline
from backend.reconstruction.service import reconstruct
from backend.reconstruction.models import digest

FIELDS = ('course_code','course_name','session_type','section','lecturer','day_of_week',
          'date','start_time','end_time','location','teaching_weeks','excluded_weeks','recurrence')


def normalized(value):
    if isinstance(value,str):
        return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())
    if isinstance(value,dict): return {k:normalized(v) for k,v in value.items()}
    if isinstance(value,list): return [normalized(v) for v in value]
    return value


def score(predictions, references, fields):
    """Exact tuple multiset intersection: one reference cannot match twice."""
    if not references and not predictions:
        return dict(tp=0,fp=0,fn=0,duplicates=0,precision=None,recall=None,f1=None)
    key=lambda e:json.dumps([normalized(e.get(f)) for f in fields],sort_keys=True)
    actual=Counter(key(p) for p in predictions); expected=Counter(key(r) for r in references)
    tp=sum((actual & expected).values());fp=len(predictions)-tp;fn=len(references)-tp
    return dict(tp=tp,fp=fp,fn=fn,duplicates=sum(max(0,n-1) for n in actual.values()),
        precision=tp/len(predictions) if predictions else None,
        recall=tp/len(references) if references else None,
        f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)


def save(path,value):
    with Path(path).open('x',encoding='utf-8') as f:json.dump(value,f,indent=2)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capture');c.add_argument('--source',type=Path,required=True);c.add_argument('--out',type=Path,required=True)
    c=sub.add_parser('score');c.add_argument('--run',type=Path,required=True);c.add_argument('--references',type=Path,required=True);c.add_argument('--out',type=Path,required=True)
    a=p.parse_args(argv)
    if a.command=='capture':
        if a.out.exists(): raise ValueError('Use a new output directory')
        a.out.mkdir(parents=True)
        raw=extract_document(a.source.name,None,a.source.read_bytes(),force_ocr=True)
        packet=adapt_extraction(raw)
        runs={}
        for method in ('B1','B2','B2H-v1'):
            try:runs[method]={'packet':adapt_baseline(raw,method).model_dump(mode='json')}
            except ValueError as e:runs[method]={'failure_type':type(e).__name__}
        runs['P1']=reconstruct(packet,'constrained')
        save(a.out/'run.json',{'scope':'DEVELOPMENT_ONLY','source_sha256':raw.checksum,
            'evidence_digest':digest(packet.model_dump(mode='json')),'raw_evidence':raw.model_dump(mode='json'),
            'conditions':'Shared full-page OCR; P1 m4 development; RT4/S2b excluded to isolate shared-evidence comparison',
            'runs':runs})
        save(a.out/'references.blank.json',{'scope':'DEVELOPMENT_ONLY','source_sha256':raw.checksum,
            'checked_by':None,'checked_at':None,'all_visible_sessions_checked':False,
            'fields':list(FIELDS),'pages':[{'page':pg.page,'events':[]} for pg in raw.pages],
            'instructions':'Manually enumerate source events, independently of predictions. Every scored field must be explicit; null means source absence only after checking. Ambiguous applicable fields require resolving or a separately declared reduced metric. Do not claim independent Gold.'})
        return
    run=json.loads(a.run.read_text(encoding='utf-8'));ref=json.loads(a.references.read_text(encoding='utf-8'))
    if run['scope']!='DEVELOPMENT_ONLY' or ref['scope']!='DEVELOPMENT_ONLY':raise ValueError('Development only')
    if ref['source_sha256']!=run['source_sha256'] or not ref['checked_by'] or not ref['checked_at'] or ref['all_visible_sessions_checked'] is not True:
        raise ValueError('Matching source and actual manual-check records required')
    fields=ref['fields']
    if not fields or len(set(fields))!=len(fields) or set(fields)-set(FIELDS):raise ValueError('Invalid metric fields')
    pages=ref['pages'];pageids=[x['page'] for x in pages]
    if len(set(pageids))!=len(pageids) or set(pageids)!={x['page'] for x in run['raw_evidence']['pages']}:raise ValueError('Reference page coverage mismatch')
    for pg in pages:
        for event in pg['events']:
            if not set(fields)<=set(event):raise ValueError('Missing reference fields')
    report={'scope':'DEVELOPMENT_MANUALLY_CHECKED_NOT_INDEPENDENT_GOLD','metric_fields':fields,
            'reference_digest':digest(ref),'results':{},'error_attribution':'Inspect saved raw_evidence against source for OCR misses; inspect predictions against the same evidence for grouping/selection errors. Automatic attribution unavailable.'}
    for method,result in run['runs'].items():
        packet=result.get('packet') or (result.get('canonical_output') or {}).get('packet')
        if packet is None:report['results'][method]={'failure':True};continue
        totals=Counter();records=[]
        for pg in pages:
            ev=[]
            spans={s['evidence_id']:s.get('page') for s in packet['evidence']}
            for e in packet['events']:
                owned={spans.get(i) for o in e['revision']['observations'] for i in o['evidence_ids']}-{None}
                if owned=={pg['page']}:ev.append(e['revision']['content'])
                elif pg is pages[0] and len(owned)!=1:raise ValueError('Prediction page ownership unresolved; retain failure instead of silently scoring')
            s=score(ev,pg['events'],fields);records.append({'page':pg['page'],**s})
            totals.update({k:s[k] for k in ('tp','fp','fn','duplicates')})
        tp,fp,fn=(totals[k] for k in ('tp','fp','fn'))
        report['results'][method]={**totals,'precision':tp/(tp+fp) if tp+fp else None,
            'recall':tp/(tp+fn) if tp+fn else None,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,'pages':records}
    save(a.out,report)


if __name__=='__main__':main()
