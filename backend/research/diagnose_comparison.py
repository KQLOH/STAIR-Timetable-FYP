"""Development diagnostics; closest-reference links never alter exact scores."""
import argparse
import json
from pathlib import Path
from collections import Counter
from datetime import datetime
from backend.research.development_compare import FIELDS, normalized, score, save
from backend.services.file_extractor import extract_document
from backend.services.extraction_adapter import adapt_extraction
from backend.layout.block_capture import BlockCollector
from backend.layout.block_models import BlockEvidence
from backend.layout.block_grid import reconstruct_blocks, assess_blocks

CORE=('course_code','session_type','day_of_week','start_time','end_time')


def diagnostic(predictions,references,fields):
    rows=[]
    for index,event in enumerate(predictions):
        similarities=[sum(normalized(event.get(f))==normalized(r.get(f)) and r.get(f) is not None
                          for f in CORE+('location',)) for r in references]
        best=max(similarities,default=0)
        tied=[i for i,n in enumerate(similarities) if n==best]
        selected=references[tied[0]] if tied else None
        rows.append({'prediction':index,'diagnostic_only':True,'closest_reference_indices':tied,
            'similarity_fields':best,'plausible_link':best>=2,'values':event,
            'differences':{f:{'prediction':event.get(f),'reference':selected.get(f)} for f in fields
                if selected is not None and normalized(event.get(f))!=normalized(selected.get(f))},
            'missing_fields':[f for f in fields if event.get(f) is None]})
    return rows


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--references',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args(argv)
    if a.out.exists():raise ValueError('Use a new output directory')
    run=json.loads(a.run.read_text(encoding='utf-8-sig'));ref=json.loads(a.references.read_text(encoding='utf-8-sig'))
    if run['scope']!='DEVELOPMENT_ONLY' or ref['scope']!='DEVELOPMENT_ONLY':raise ValueError('Development only')
    if not ref['checked_by'] or not ref['all_visible_sessions_checked']:raise ValueError('Manual check record required')
    datetime.fromisoformat(ref['checked_at'])
    collector=BlockCollector()
    raw=extract_document(a.source.name,None,a.source.read_bytes(),force_ocr=True,collector=collector)
    if raw.checksum!=run['source_sha256'] or raw.checksum!=ref['source_sha256']:raise ValueError('Source hash mismatch')
    packet=adapt_extraction(raw)
    supplement=BlockEvidence(source_document_id=raw.source_id,content_sha256=raw.checksum,
        pages=tuple(collector),correction_mode='context-v1')
    full,_,_=reconstruct_blocks(packet,supplement)
    policy=assess_blocks(packet,supplement,full)
    conditions=dict(run['runs']);conditions['STAIR-S2b-RT4']=full
    report={'scope':'DEVELOPMENT_ONLY','reference_authenticity':'User reports manual check; metadata is an attestation, not authenticated proof',
        'preprocessing':'B1/B2/B2H/P1 share saved full-page OCR; STAIR-S2b-RT4 uses fresh chromatic regions and per-block OCR',
        'metrics':{},'diagnostics':{},'warnings':[],'policy_decisions':[d.decision for d in policy]}
    if any(e.get('teaching_weeks')==[] or e.get('excluded_weeks')==[] for pg in ref['pages'] for e in pg['events']):
        report['warnings'].append('Empty week arrays are distinct from unknown/null; source applicability needs human confirmation; labels preserved')
    for method,result in conditions.items():
        co=result.get('canonical_output') or {};pred_packet=result.get('packet') or co.get('packet')
        if pred_packet is None:report['metrics'][method]={'failure':True};continue
        spans={s['evidence_id']:s.get('page') for s in pred_packet['evidence']}
        allrows=[];metrics={name:Counter() for name in ('core','core_venue','strict_declared')}
        for pg in ref['pages']:
            selected=[]
            for event in pred_packet['events']:
                ownership={spans.get(i) for o in event['revision']['observations'] for i in o['evidence_ids']}-{None}
                if len(ownership)!=1:raise ValueError('Unresolved prediction page ownership')
                if ownership=={pg['page']}:selected.append(event['revision']['content'])
            references=pg['events']
            for name,fields in [('core',CORE),('core_venue',CORE+('location',)),('strict_declared',tuple(ref['fields']))]:
                stats=score(selected,references,fields)
                metrics[name].update({k:stats[k] for k in ('tp','fp','fn','duplicates')})
            allrows.extend(diagnostic(selected,references,ref['fields']))
        report['metrics'][method]={}
        for name,counts in metrics.items():
            tp,fp,fn=(counts[k] for k in ('tp','fp','fn'))
            report['metrics'][method][name]={**counts,'precision':tp/(tp+fp) if tp+fp else None,
                'recall':tp/(tp+fn) if tp+fn else None,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}
        report['diagnostics'][method]={'selected_events':allrows,
            'unresolved_candidates_excluded_from_reconstructed_scores':len(co.get('unresolved_candidates',[]))}
    a.out.mkdir(parents=True)
    save(a.out/'full-application.json',{'extraction':packet.model_dump(mode='json'),
        'layout_evidence':supplement.model_dump(mode='json'),'reconstruction':full,
        'representation':'Selected reconstructed events only; unresolved projections retained separately; no human review applied'})
    save(a.out/'diagnosis.json',report)
    print(json.dumps({'metrics':report['metrics'],'warnings':report['warnings'],
        'policy_decisions':report['policy_decisions']}))


if __name__=='__main__':main()
