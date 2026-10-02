"""Summarize paired synthetic Router probes; never call a provider."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics


def summarize(folder):
    meta=json.loads((folder/'metadata.json').read_text())
    rows=[json.loads(line) for line in (folder/'results.jsonl').read_text().splitlines() if line]
    seen=[(r['case_id'],r['repeat'],r['arm']) for r in rows]
    assert len(seen)==len(set(seen)), 'Duplicate paid observations'
    expected=meta['cases']*meta['repeats']*2
    report={'completed_requests':len(rows),'expected_requests':expected,'complete':len(rows)==expected,'arms':{},'failures':[]}
    allowed_tasks=set(json.loads((folder/'tool-schema.json').read_text())['function']['parameters']['properties']['knowledge_task']['enum'])
    pairs=defaultdict(dict)
    for row in rows:pairs[(row['case_id'],row['repeat'])][row['arm']]=row
    report['paired_inputs_identical']=all(
        pair['json']['user_hash']==pair['tool']['user_hash'] and pair['json']['base_system_hash']==pair['tool']['base_system_hash']
        for pair in pairs.values() if len(pair)==2)
    report['paired_success']=dict(Counter((
        'both_correct' if all(r['valid'] and r['correct'] for r in p.values()) else
        'tool_only_correct' if p['tool']['valid'] and p['tool']['correct'] else
        'json_only_correct' if p['json']['valid'] and p['json']['correct'] else 'both_incorrect')
        for p in pairs.values() if len(p)==2))
    for arm in ['json','tool']:
        selected=[r for r in rows if r['arm']==arm]
        if not selected:continue
        usage=Counter();observed=0;strict=0;scope_invalid=0
        for r in selected:
            for c in r['calls']:
                if c.get('usage'):
                    observed+=1
                    usage.update({k:v for k,v in c['usage'].items() if type(v) is int})
            if arm=='tool':
                strict+=int(r['valid'])
                scope_invalid+=sum(c.get('task_scope_valid') is False for c in r['calls'])
            else:
                try:
                    payload=json.loads(r['calls'][-1]['text'])
                    strict+=int(isinstance(payload,dict) and set(payload)=={'target_module','knowledge_task'}
                        and type(payload['target_module']) is str and payload['target_module'] in ['1','2','3','4']
                        and type(payload['knowledge_task']) is str and payload['knowledge_task'] in allowed_tasks)
                    if isinstance(payload,dict) and payload.get('knowledge_task') in allowed_tasks:
                        task=payload['knowledge_task']
                        scope_invalid+=int(task!='general' and task[1]!=str(payload.get('target_module')))
                except (IndexError,KeyError,TypeError,ValueError):pass
        by_case=defaultdict(list)
        for r in selected:by_case[r['case_id']].append(r)
        repeated=[rs for rs in by_case.values() if len(rs)==meta['repeats']]
        latencies=sorted(r['duration_ms']/1000 for r in selected)
        report['arms'][arm]={
            'n':len(selected),'valid':sum(r['valid'] for r in selected),
            'correct_valid':sum(r['valid'] and r['correct'] for r in selected),
            'strict_two_field_shape':strict,'raw_task_scope_invalid':scope_invalid,
            'false_transitions':sum(r['valid'] and r['expected_module']==r['current_module'] and r['decision']['target_module']!=r['current_module'] for r in selected),
            'missed_transitions':sum(r['expected_module']!=r['current_module'] and (not r['valid'] or r['decision']['target_module']==r['current_module']) for r in selected),
            'task_label_correct':sum(r['valid'] and r.get('task_correct') is True for r in selected),
            'task_label_count':sum(r.get('expected_task') is not None for r in selected),
            'repeat_module_agreement':sum(len({r.get('decision',{}).get('target_module') for r in rs})==1 and all(r['valid'] for r in rs) for rs in repeated),
            'repeated_case_count':len(repeated),
            'latency_median_seconds':round(statistics.median(latencies),3),
            'latency_mean_seconds':round(statistics.mean(latencies),3),
            'latency_max_seconds':round(max(latencies),3),
            'provider_attempts':sum(len(r['calls']) for r in selected),
            'observed_usage_responses':observed,'usage':dict(usage)}
        for r in selected:
            if not r['valid'] or not r['correct']:
                report['failures'].append({k:r.get(k) for k in ['case_id','repeat','arm','expected_module','decision','error']})
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('folder',type=Path);args=p.parse_args()
    result=summarize(args.folder)
    (args.folder/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
