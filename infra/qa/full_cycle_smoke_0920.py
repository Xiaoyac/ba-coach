"""Read-only supplementary isolation/history checks on synthetic accounts."""
import json
from pathlib import Path
import requests
from full_cycle_acceptance_0920 import Client,OUT,BASE,save

def main():
    a,b=Client('A'),Client('B')
    da=json.loads((OUT/'A/01_散步.json').read_text(encoding='utf-8'))
    ds=json.loads((OUT/'A/11_独立次要目标.json').read_text(encoding='utf-8'))
    primary=a.api('GET','/api/program/'+da['session_id'])
    secondary=a.api('GET','/api/program/'+ds['session_id'])
    probes={}
    probes['unauthenticated_list_status']=requests.get(BASE+'/api/conversations',timeout=20).status_code
    probes['foreign_conversation_status']=b.s.get(BASE+'/api/conversations/'+da['session_id'],timeout=20).status_code
    probes['foreign_program_status']=b.s.get(BASE+'/api/program/'+da['session_id'],timeout=20).status_code
    pg=primary['runtime']['active_goal_id']
    sg=secondary['runtime']['active_goal_id']
    probes['foreign_goal_history_status']=b.s.get(BASE+'/api/program/goals/'+pg+'/history',timeout=20).status_code
    probes['independent_secondary_created']=bool(sg and sg!=pg and secondary.get('goal_context',{}).get('goal_kind')=='secondary')
    original_goal=next(t['program']['runtime']['active_goal_id'] for t in da['turns']
        if t.get('program',{}).get('runtime',{}).get('active_goal_id'))
    probes['primary_binding_retained']=bool(pg==original_goal)
    probes['secondary_context']=secondary.get('goal_context')
    probes['public_home_status']=requests.get(BASE+'/',timeout=20).status_code
    checks=[]
    for actor in ('A','B','C'):
        c=Client(actor)
        for path in sorted((OUT/actor).glob('*.json')):
            if 'credentials' in path.name:continue
            d=json.loads(path.read_text(encoding='utf-8'))
            if not d.get('turns'):continue
            detail=c.api('GET','/api/conversations/'+d['session_id'])
            saved=[m['content'] for m in detail.get('messages',[]) if m['role']=='assistant']
            finished=[t for t in d['turns'] if t.get('ok')]
            checks.append(dict(actor=actor,label=d['label'],nonempty_stream_replies=len(finished),
                all_replies_persisted=all(t['reply'] in saved for t in finished)))
    probes['persisted_checks']=checks
    save(OUT/'smoke.json',probes)
    print(json.dumps(probes,ensure_ascii=False,indent=2))

if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    main()
