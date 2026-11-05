"""Bounded, field-local OCR alternatives; no venue dictionary or Gold inputs.

A format witness is an uncorrected reading from a different source region.
Two distinct values/regions are required; corrections never become witnesses.
"""
import re

VERSION='rt4-context-v1'
TO_DIGIT={'O':'0','I':'1','L':'1','S':'5','B':'8','Z':'2'}
TO_LETTER={'0':'O','1':'I','5':'S','8':'B','2':'Z',')':'J'}


def signature(text):
    text=re.sub(r'\s+','',text).upper()
    if not re.fullmatch(r'[A-Z]{1,6}-?\d{2,4}[A-Z]?',text):return None
    return ''.join('A' if c.isalpha() else 'D' if c.isdigit() else c for c in text)


def established_formats(witnesses,region_id):
    grouped={}
    for w in witnesses:
        if w['region_id']==region_id:continue
        key=signature(w['text'])
        if key and w.get('confidence') is not None and w['confidence']>=.75:grouped.setdefault(key,[]).append(w)
    return {key:ws for key,ws in grouped.items() if len({w['region_id'] for w in ws})>=2 and len({w['text'].upper() for w in ws})>=2}


def _patterns(field):
    for prefix in range(1 if field=='location' else 2,5 if field=='location' else 7):
        for count in range(2 if field=='location' else 3,5):
            for suffix in range(2):
                for hyphen in ([''] if field=='location' else ['','-']):
                    yield 'A'*prefix+hyphen+'D'*count+'A'*suffix


def _fit(text,pattern):
    if len(text)!=len(pattern):return None
    value=[];changes=[]
    for i,(c,kind) in enumerate(zip(text,pattern)):
        converted=c
        if kind=='D' and not c.isdigit():converted=TO_DIGIT.get(c)
        elif kind=='A' and not c.isalpha():converted=TO_LETTER.get(c)
        elif kind=='-' and c!='-':return None
        if converted is None:return None
        value.append(converted)
        if converted!=c:changes.append({'position':i,'original':c,'suggested':converted,'role':'numeric' if kind=='D' else 'alphabetic'})
    if not 1<=len(changes)<=3:return None
    return ''.join(value),changes


def correct_field(field,raw,*,formats=None,alternatives=()):
    """Confidence is recorded by the adapter, never a substitute for support."""
    formats=formats or {};compact=re.sub(r'\s+','',raw).upper()
    alts={re.sub(r'\s+','',a).upper() for a in alternatives}
    result={'field':field,'original':raw,'suggested':None,'status':'UNCHANGED','source_supported':False,
            'rule':None,'position_space':'whitespace-stripped-uppercase-field','candidates':[],'alternative_readings':list(alternatives),'version':VERSION}
    if field=='lecturer':return result  # Names are never character-normalized.
    if field in {'location','course_code'}:
        # Alphabetic/free-text locations are legitimate, not numeric-code guesses.
        if field=='location' and (re.fullmatch(r'[A-Za-z]+(?: [A-Za-z]+)+',raw) or not any(c.isdigit() for c in compact)):
            return result
        if len(compact)>20:return result
        current=signature(compact)
        candidates=[]
        for pattern in _patterns(field):
            fit=_fit(compact,pattern)
            if not fit:continue
            value,changes=fit
            observed=value in alts
            supported=pattern in formats and all(c['role']=='numeric' for c in changes)
            candidates.append({'value':value,'pattern':pattern,'changes':changes,'observed_in_crop':observed,
                               'format_supported':supported,'rank':(0 if observed else 1 if supported else 2,len(changes))})
        # An already established valid interpretation wins over speculative edits.
        if current in formats:return result
        # Do not flag ordinary well-formed digit runs merely because a digit can
        # also resemble a suffix letter; only mixed numeric positions/alternates.
        candidates=[c for c in candidates if c['observed_in_crop'] or any(x['role']=='numeric' or x['original']==')' for x in c['changes'])]
    elif field=='session_type':
        if compact in {'L','P','T'}:return result
        candidates=[{'value':v,'changes':[],'observed_in_crop':True,'format_supported':False,'rank':(0,1)} for v in alts if v in {'L','P','T'} and (compact,v) in {('1','T'),('7','T'),('I','L'),('1','L')}]
        if not candidates and compact in {'1','7','I'}:
            candidates=[{'value':v,'changes':[],'observed_in_crop':False,'format_supported':False,'rank':(2,1)} for v in ('T','L') if compact!='7' or v=='T']
    elif field=='time_range':
        # Only digits adjacent to the printed colon, with an explicit AM/PM
        # range. No repairs to meridiem, date, duration or lecturer prose.
        from backend.reconstruction.candidates import TIME_RE
        if TIME_RE.fullmatch(raw):return result
        candidate=re.sub(r'[0-9OIlLSBZ]{1,2}:[0-9OIlLSBZ]{2}',lambda m:''.join(TO_DIGIT.get(c.upper(),c) if c!=':' else ':' for c in m.group()),raw)
        candidates=[]
        from backend.services.normalizer import normalize_time
        match=TIME_RE.fullmatch(candidate)
        values=[normalize_time(match[i]) for i in (1,2)] if match else []
        if candidate!=raw and values and all(values) and values[0]<values[1] and all(re.search(r'[AP]M',match[i],re.I) for i in (1,2)):
            candidates=[{'value':candidate,'changes':[],'observed_in_crop':re.sub(r'\s+','',candidate).upper() in alts,'format_supported':False,'rank':(0,1)}]
    else:return result
    if field in {'location','course_code'} and current and not any(c['observed_in_crop'] for c in candidates):return result
    candidates.sort(key=lambda c:(c['rank'],c['value']))
    unique={c['value']:c for c in candidates};candidates=list(unique.values())[:32]
    result['candidates']=candidates
    if not candidates:return result
    supported=[c for c in candidates if c['observed_in_crop'] or c['format_supported']]
    # A valid competing OCR reading cannot be voted away by format frequency.
    competing_valid=field in {'location','course_code'} and current is not None
    if len(supported)==1 and not competing_valid:
        chosen=supported[0];result.update(suggested=chosen['value'],status='SUPPORTED',source_supported=True,
            rule='same-region-alternative-OCR' if chosen['observed_in_crop'] else 'independent-format-numeric-position')
    else:
        result.update(suggested=candidates[0]['value'] if len(candidates)==1 else None,status='AMBIGUOUS',rule='insufficient-or-competing-source-support')
    return result
