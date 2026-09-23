"""Reproducible evidence metrics (no credentials in output)."""
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'work/full-cycle-acceptance-0920'

def dist(values):
    values=sorted(v/1000 for v in values if isinstance(v,(int,float)))
    if not values: return None
    return {'n':len(values),'mean_s':round(statistics.mean(values),2),
        'median_s':round(statistics.median(values),2),'p95_s':round(values[math.ceil(len(values)*.95)-1],2),
        'max_s':round(max(values),2)}

def summarize():
    scenarios=[]
    turns=[]
    findings=[]
    for path in sorted(OUT.glob('*/*.json')):
        if 'credentials' in path.name: continue
        d=json.loads(path.read_text(encoding='utf-8'))
        if 'turns' not in d: continue
        rows=d['turns']
        completed=[t for t in rows if 'ok' in t]
        modules=[t.get('program',{}).get('runtime',{}).get('current_module') for t in completed]
        goals={t.get('program',{}).get('runtime',{}).get('active_goal_id') for t in completed}-{None}
        initial=d.get('initial_program',{}).get('runtime',{}).get('current_module')
        reached=set(modules)-{None}
        if initial: reached.add(initial)
        # Batch outcomes remain immutable evidence of the bounded script run.
        # Follow-up diagnostic turns can advance a previously stalled case.
        cycle_trial=bool(d.get('intended'))
        closed_cycles={c['id'] for t in completed
            for h in (t.get('history') or {}, t.get('tracked_history') or {})
            for c in h.get('cycles',[]) if c.get('status')=='completed'}
        scenarios.append(dict(actor=path.parent.name,label=d['label'],session_id=d['session_id'],
            result=d.get('result'),turns=len(completed),initial_module=d.get('initial_program',{}).get('runtime',{}).get('current_module'),
            reached=sorted(reached),goals=len(goals),cycle_trial=cycle_trial,
            final_module=modules[-1] if modules else initial,
            closed_cycles=len(closed_cycles),canonical=d.get('canonical_choice_wording',False)))
        for i,t in enumerate(rows,1):
            if 'ok' not in t: continue
            turns.append(t)
            ref=f'{path.parent.name}/{d["label"]}#{i}'
            reply=t.get('reply','')
            if 'DSML' in reply or '<tool_call' in reply:
                findings.append(dict(ref=ref,kind='tool_markup',reply=reply))
            if re.search(r'1\s*(?:[–—-]|到|至)\s*10', reply):
                findings.append(dict(ref=ref,kind='rating_scale_1_10',reply=reply))
            if not t.get('ok'): findings.append(dict(ref=ref,kind='request_failed',error=t.get('error'),
                events=[e.get('data') for e in t.get('events',[]) if e.get('event')=='error']))
            if (t.get('elapsed_ms') or 0)>40000: findings.append(dict(ref=ref,kind='slow_reply',ms=t['elapsed_ms']))
    trials=[s for s in scenarios if s['cycle_trial']]
    def rate(n,d):
        return dict(success=n,eligible=d,percent=round(100*n/d,2) if d else None)
    m1=[s for s in trials if s['initial_module']=='module_1']
    m2=[s for s in trials if 'module_2' in s['reached']]
    m3=[s for s in trials if 'module_3' in s['reached']]
    m4=[s for s in trials if 'module_4' in s['reached']]
    stages=dict(m1_to_m2=rate(sum('module_2' in s['reached'] for s in m1),len(m1)),
        m2_goal_created=rate(sum(s['goals']>0 for s in m2),len(m2)),
        m2_to_m3=rate(sum('module_3' in s['reached'] for s in m2),len(m2)),
        m3_to_m4=rate(sum('module_4' in s['reached'] for s in m3),len(m3)),
        m4_cycle_closed=rate(sum(s['closed_cycles']>0 for s in m4),len(m4)),
        closed_cycle_trials=rate(sum(s['closed_cycles']>0 for s in trials),len(trials)),
        full_m1_to_closed_cycle=rate(sum(s['closed_cycles']>0 for s in m1),len(m1)))
    knowledge=[t['knowledge'] for t in turns if t.get('knowledge')]
    router_messages=[t.get('detail',{}).get('messages',[{}])[-1] for t in turns
        if t.get('detail',{}).get('messages')]
    router=dict(captured=len(router_messages),
        with_model_metadata=sum(bool(m.get('router_model_name')) for m in router_messages),
        with_duration=sum(isinstance((m.get('timing') or {}).get('router_processing_ms'),(int,float)) for m in router_messages),
        m2_recommended_m1_retained=sum('Router建议：module_2；后台核验后的实际阶段：module_1' in
            (m.get('routing_reasoning_content') or '') for m in router_messages))
    rag=dict(captured=len(knowledge),
        retrieval_outcomes=dict(Counter(k.get('retrieval_outcome') for k in knowledge)),
        validator_statuses=dict(Counter(k.get('validator_status') for k in knowledge)),
        withheld=sum(bool(k.get('context_withheld')) for k in knowledge),
        withheld_with_provided=sum(bool(k.get('context_withheld') and k.get('provided')) for k in knowledge),
        completed_with_guidance=sum(k.get('mediator_status')=='completed' and bool(k.get('mediator_guidance')) for k in knowledge))
    return dict(scenarios=len(scenarios),finished_scenarios=sum(bool(s['result']) for s in scenarios),
        cycle_trials=len(trials),finished_cycle_trials=sum(bool(s['result']) for s in trials),
        stages=stages,latest_trial_modules=dict(Counter(s['final_module'] for s in trials)),
        turns=len(turns),transport_ok=sum(bool(t['ok']) for t in turns),
        empty_replies=sum(not bool(t.get('reply','').strip()) for t in turns),
        router=router,rag=rag,
        outcomes=dict(Counter(s['result']['outcome'] for s in scenarios if s['result'])),
        first_content=dist([t.get('first_content_ms') for t in turns]),
        response=dist([t.get('elapsed_ms') for t in turns]),settled=dist([t.get('settled_ms') for t in turns]),
        mediator=dict(Counter(t.get('knowledge',{}).get('mediator_status','not_recorded') for t in turns)),
        findings=findings,scenario_details=scenarios)

if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    result=summarize()
    if '--save' in sys.argv:
        from full_cycle_acceptance_0920 import save
        save(OUT/'summary.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
