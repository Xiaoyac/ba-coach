"""Offline comparison of captured successive drafts; no provider/DB calls."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app.dialogue_confirmation import affirmative,fingerprint,summary_present,_CONFIRMATION_FIELDS

def audit():
    result=[]
    for path in sorted((ROOT/'work/full-cycle-acceptance-0920').glob('*/*.json')):
        if 'credentials' in path.name: continue
        data=json.loads(path.read_text(encoding='utf-8'))
        turns=data.get('turns',[])
        for i in range(1,len(turns)):
            prev,row=turns[i-1],turns[i]
            if not row.get('ok') or not affirmative(row['user']): continue
            before=prev.get('program',{}).get('draft')
            after=row.get('program',{}).get('draft')
            module=prev.get('program',{}).get('runtime',{}).get('current_module')
            if not before or not after or module not in ('module_2','module_3'): continue
            result.append(dict(ref=f'{path.parent.name}/{data["label"]}#{i+1}',module=module,
                next_module=row['program']['runtime']['current_module'],
                summary_matches=summary_present(module,prev['reply'],after),
                fingerprint_matches=fingerprint(before)==fingerprint(after),
                changed_fields={k:{'before':before.get(k),'after':after.get(k)} for k in
                    (_CONFIRMATION_FIELDS if module=='module_2' else before.keys()) if before.get(k)!=after.get(k)},
                missing=row['program'].get('missing_fields')))
    return result

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(audit(),ensure_ascii=False,indent=2,default=str))
