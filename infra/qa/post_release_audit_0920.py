"""Read-only persisted-state audit of F/G synthetic acceptance accounts."""
import json
import sys
import requests
from post_release_cycles_0920 import OUT, cycle


def main():
    cycle.OUT = OUT
    clients = {}
    for actor in ('F', 'G'):
        assert (OUT / actor / 'credentials.local-only.json').exists()
        clients[actor] = cycle.Client(actor)
    checks = []
    first_goal = None
    for actor, client in clients.items():
        for path in sorted((OUT / actor).glob('*.json')):
            if 'credentials' in path.name:
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            if not data.get('intended'):
                continue
            sid = data['session_id']
            program = client.api('GET', '/api/program/' + sid)
            detail = client.api('GET', '/api/conversations/' + sid)
            runtime = program['runtime']
            gid = (data.get('result') or {}).get('goal_id') or runtime.get('active_goal_id')
            if actor == 'F' and gid:
                first_goal = first_goal or gid
            cid = (data.get('result') or {}).get('cycle_id')
            history = client.api('GET', '/api/program/goals/' + gid + '/history') if gid else {}
            completed = next((c for c in history.get('cycles', []) if c['id'] == cid and c['status'] == 'completed'), None)
            action = data['intended']['decision']
            state_checks = {'cycle_closed': bool(completed),
                'review_confirmed': bool(completed and completed.get('review_status') == 'confirmed'),
                'action_matches': bool(completed and completed.get('review_action') == action)}
            if action in {'end', 'pause'}:
                expected = 'completed' if action == 'end' else 'paused'
                state_checks['goal_status'] = history.get('goal', {}).get('status') == expected
                state_checks['flow_status'] = runtime['flow_status'] == expected
            else:
                state_checks['same_goal'] = runtime['active_goal_id'] == gid
                state_checks['new_cycle'] = bool(cid and runtime['active_cycle_id'] != cid)
                state_checks['module'] = runtime['current_module'] == ('module_4' if action == 'continue' else 'module_2')
                plans = history.get('plans', [])
                state_checks['plan_versions'] = (len(plans) == 1 if action == 'continue' else
                    len(plans) >= 2 and any(p['record_status'] == 'draft' for p in plans)
                    and any(p['record_status'] == 'confirmed' for p in plans))
            saved = [m['content'] for m in detail.get('messages', []) if m['role'] == 'assistant']
            finished = [t for t in data['turns'] if t.get('ok')]
            evidence = dict(actor=actor, label=data['label'], session_id=sid,
                action=action, intended_result=data['intended']['result'],
                checks=state_checks, complete=all(state_checks.values()),
                final_module=runtime['current_module'],
                all_replies_persisted=all(t['reply'] in saved for t in finished),
                history=history, program=program)
            cycle.save(OUT / 'audits' / (actor + '_' + path.name), evidence)
            checks.append({k: v for k, v in evidence.items() if k not in {'history', 'program'}})
    sample = next((c for c in checks if c['actor'] == 'F'), None)
    isolation = {'unauthenticated': requests.get(cycle.BASE + '/api/conversations', timeout=20).status_code}
    if sample:
        sid = sample['session_id']
        isolation['foreign_conversation'] = clients['G'].s.get(cycle.BASE + '/api/conversations/' + sid, timeout=20).status_code
        isolation['foreign_program'] = clients['G'].s.get(cycle.BASE + '/api/program/' + sid, timeout=20).status_code
    if first_goal:
        isolation['foreign_goal_history'] = clients['G'].s.get(cycle.BASE + '/api/program/goals/' + first_goal + '/history', timeout=20).status_code
    report = dict(strict_complete=sum(c['complete'] for c in checks), attempts=len(checks),
                  isolation=isolation, cases=checks)
    cycle.save(OUT / 'strict-audit.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
