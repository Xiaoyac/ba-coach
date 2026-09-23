"""Real production conversations. No force-module/confirm writes or DB changes.

Only synthetic accounts are used; raw evidence and credentials stay ignored.
Scripted personas are cooperative coverage, not unassisted usability evidence.
"""
import argparse
import concurrent.futures
import json
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
import requests

BASE = 'https://bacoach.xyz'
OUT = Path(__file__).resolve().parents[2] / 'work/full-cycle-acceptance-0920'

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.pending')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

class Client:
    def __init__(self, actor):
        self.folder = OUT / actor
        self.s = requests.Session()
        self.folder.mkdir(parents=True, exist_ok=True)
        creds = self.folder / 'credentials.local-only.json'
        if creds.exists():
            a = json.loads(creds.read_text(encoding='utf-8'))
        else:
            suffix = uuid.uuid4().hex[:10]
            payload = dict(username='qa0920'+suffix, password='Qa0920!'+uuid.uuid4().hex,
                email='qa0920'+suffix+'@example.com', nickname='周期验收0920'+actor,
                tag=str(int(suffix,16)%100000).zfill(5), birth_date='1995-06-15',
                living_status='和家人', communication_preference='温柔引导',
                physical_condition=[], behavior_taboo=[])
            a = self.api('POST','/api/auth/register',json=payload)
            save(creds, {**a,'username':payload['username'],'password':payload['password']})
        self.s.headers['Authorization'] = 'Bearer '+a['token']

    def api(self, method, path, **kwargs):
        r = self.s.request(method,BASE+path,timeout=(15,120),**kwargs)
        r.raise_for_status()
        return r.json()

    def new(self, label):
        c = self.api('POST','/api/conversations')
        data = dict(label=label,session_id=c['session_id'],initial=c,turns=[])
        save(self.folder/(label+'.json'),data)
        return data

    def say(self, data, text):
        if (OUT/'pause.flag').exists():
            raise RuntimeError('Operator pause at safe turn boundary')
        sid = data['session_id']
        row = dict(user=text,reply='',events=[],started_at=datetime.now().isoformat(),first_content_ms=None)
        data['turns'].append(row)
        path = self.folder/(data['label']+'.json')
        save(path,data)
        started = time.perf_counter()
        try:
            with self.s.post(BASE+'/api/chat/stream',json=dict(session_id=sid,provider='deepseek',
                 message=text,generation_id=str(uuid.uuid4())),stream=True,timeout=(15,100)) as response:
                row['http_status']=response.status_code
                response.raise_for_status()
                response.encoding='utf-8'
                event='message'
                for line in response.iter_lines(decode_unicode=True):
                    if line.startswith('event:'):
                        event=line[6:].strip()
                    elif line.startswith('data:'):
                        body=json.loads(line[5:])
                        ms=round((time.perf_counter()-started)*1000)
                        row['events'].append(dict(event=event,data=body,elapsed_ms=ms))
                        if event=='delta':
                            row['first_content_ms']=row['first_content_ms'] or ms
                            row['reply']+=body.get('text','')
            row['elapsed_ms']=round((time.perf_counter()-started)*1000)
            row['ok']=bool(row['reply'] and any(e['event']=='done' for e in row['events'])
                and not any(e['event']=='error' for e in row['events']))
            save(path,data)
            # Native reasoning may legitimately be absent. Router model/timing
            # metadata, not private reasoning text, signals persisted routing.
            until=time.monotonic()+55
            while True:
                detail=self.api('GET','/api/conversations/'+sid)
                last=(detail.get('messages') or [{}])[-1]
                if last.get('role')=='assistant' and (last.get('router_model_name')
                    or (last.get('timing') or {}).get('router_processing_ms') is not None):
                    break
                if time.monotonic()>=until or not row['ok']:
                    break
                time.sleep(2)
            row['detail']=detail
            row['program']=self.api('GET','/api/program/'+sid)
            row['overview']=self.api('GET','/api/program/goals/overview')
            goal=row['program'].get('runtime',{}).get('active_goal_id')
            if goal:
                row['history']=self.api('GET',f'/api/program/goals/{goal}/history')
            if last.get('id') and last.get('role')=='assistant':
                row['knowledge']=self.api('GET',f"/api/conversations/messages/{last['id']}/knowledge")
            row['settled_ms']=round((time.perf_counter()-started)*1000)
        except Exception as e:
            row['error']=type(e).__name__+': '+str(e)
            row['ok']=False
        finally:
            save(path,data)
        rt=row.get('program',{}).get('runtime',{})
        print(json.dumps(dict(actor=self.folder.name,case=data['label'],turn=len(data['turns']),
            ok=row['ok'],ms=row.get('elapsed_ms'),module=rt.get('current_module'),
            flow=rt.get('flow_status'),reply=row['reply'][:180]),ensure_ascii=False),flush=True)
        return row

M1=[
 '最近下班后我总是没精神，想活动又拖着不动，有些烦躁和自责。我想聊聊这个问题。',
 '昨天晚饭后我看到门口的运动鞋，本来想出门走走。我想今天太累了明天再说，身体沉重，心情低落，于是躺在沙发刷手机两个小时。当时松口气，后来更自责，更不想动了。',
 '我试过强迫自己每天跑半小时，坚持两天就放弃；后来试过把鞋放门口偶尔有帮助，先走五分钟比跑步容易。你总结的情境、想法、情绪身体感受、行为和后果准确，符合我的经历。',
 '我理解到低落时回避活动会暂时轻松，却减少积极体验，让情绪更差；先做一个小而可行的活动，再观察感受，可以慢慢打破这个循环，不是等心情好才动，也不保证马上开心。这个思路和我的经历贴合。',
 '我没有疑问了，也愿意尝试。请继续和我讨论一个自己愿意做的小活动目标。',
 '是的，你对我的理解准确；我已经理解这个原理，也确认愿意一起制定下一步的小行动，不需要继续重复确认。',
 '我理解了，也愿意进入目标设定。',
 '可以，我们开始讨论具体的活动安排吧。',
]

CASES=[
 ('散步','小区平路',10,'下雨','改在楼道走五分钟','complete','end'),
 ('站立伸展','客厅',5,'工作忙','睡前站着伸展两分钟','complete','end'),
 ('走廊步行','办公室走廊',10,'加班','先走三分钟','partial','end'),
 ('整理房间','卧室',10,'太累','先整理两分钟','not_started','pause'),
 ('跟音乐跳舞','客厅',5,'没兴致','先跳一分钟','no_improvement','end'),
 ('站桩','阳台',5,'工作忙','先站两分钟','complete','continue'),
 ('楼下散步','楼下花园',10,'下雨','楼道走五分钟','partial','adjust'),
 ('整理书架','书房',10,'疲惫','先整理两分钟','alternative','end'),
 ('站着浇花','阳台',5,'忘记','晚饭后看花盆','complete','end'),
 ('原地踏步','客厅',5,'工作忙','先踏步一分钟','complete','end'),
]

def templates(case, canonical=False):
    activity,place,duration,barrier,coping,result,decision=case
    start=(datetime.now()+timedelta(days=1)).strftime('%Y年%m月%d日')+'20:00'
    m2=[f'我理解PA包括日常身体活动，不只是运动。我想长期恢复生活节奏，这次自己选择{activity}作为主要目标。开始时间{start}，在{place}做{duration}分钟，每天一次，先试三天。难度2分，我觉得做得到，没有身体限制，不需要同伴。可能遇到{barrier}，应对是{coping}。请和我整理这个计划。',
        '对，我自己愿意做，也有场地和时间，可以做到。请把刚才讨论的活动、开始日期时间、地点、时长、频率、障碍和应对整理成计划让我确认。',
        '确认，就按这个计划试试。',
        '我同意刚才的完整计划。请继续告诉我怎么记录每天的执行和感受。',
        '这些安排就是我想做的。请再完整总结一次计划并问我确认。',
        '确认，就按这个计划试试。']
    m3=['我愿意每天晚上做完后，打开左侧记录今日，记录活动时间、活动内容和做完后的心情，心情用0到5分；其他感受有空再填，遇到困难回来聊天。请整理我们约好的记录方法让我确认。',
        '我确认并同意这个记录方法。', '可以，就按刚才这个方式记录。',
        '记录时间是每天晚上，记录方式是网页左侧记录今日，内容是活动时间、活动内容和做完心情，其他感受选填。我同意这样记录。',
        '对，你整理的记录方式准确，我确认。']
    feedback={
      'complete':f'这次我按计划完成了{activity}{duration}分钟。开始前有点懒但没有实际障碍，想着先做一点就开始了。过程中慢慢放松，完成后心情从2分变3分，身体舒展。',
      'partial':f'我实际做了{activity}三分钟后停下，计划是{duration}分钟，部分完成。因为{barrier}有点犹豫，想着先做一点也好。完成后心情从2分变3分，身体轻松一点。',
      'not_started':f'这次我没有开始{activity}，实际零分钟。太累了，我想着以后再做，躺下刷手机。当时松口气，后来失落，心情还是2分。',
      'no_improvement':f'我完成了{activity}{duration}分钟，开始前没有障碍。我本来以为会开心，做完身体舒展了，但心情还是2分没有改善。这是不是没用？',
      'alternative':f'原计划的{activity}没有做，实际零分钟。因为疲惫觉得麻烦，就放下了，但另外临时散步五分钟，做完心情从2分到3分。散步不是原计划，不要算原目标完成。'}[result]
    choice={'end':'我决定结束这个目标，不再开启下一周期。','pause':'我决定先暂停这个目标，保留历史，不是结束。',
      'continue':'我决定继续同一目标、同一计划，开启下一执行周期，不创建新目标。',
      'adjust':'我决定调整同一目标，把每次时长改成五分钟，重新讨论计划，不创建另一个目标。'}[decision]
    # The scenario advances its simulated clock through ordinary user narration;
    # no wall-clock or database timestamps are changed.
    m4=['现在已经过了约定的执行时间，我来回顾刚才的活动。'+feedback,
        '对，你总结的事件、想法、情绪和行为后果符合我的实际体验，我确认这个总结。',
        '我理解了，身体活动和心情会相互影响，先做可行的小行动再观察，做完不保证立刻开心，也不需要硬撑。我没有疑问。'+choice,
        '我理解这个思路，也确认刚才经历的总结。'+choice+'请帮我总结这一轮。',
        '对，我已经确认经历，也理解先行动、观察、调整的道理。'+choice,
        '这正是我自己的决定。'+choice,
        '我确认这次复盘，下一步选择不变。'+choice]
    if canonical:
        m2[0]=m2[0].replace('这次自己选择','我选择')
    return {'module_2':m2,'module_3':m3,'module_4':m4}

def run_actor(actor, count=10, canonical=False):
    client=Client(actor)
    for index in range(count):
        label=f'{index+1:02d}_{CASES[index%10][0]}'
        path=client.folder/(label+'.json')
        if path.exists():
            data=json.loads(path.read_text(encoding='utf-8'))
            if 'result' in data:
                continue
        else:
            data=client.new(label)
        case=CASES[index%10]
        scripts={'module_1':M1,**templates(case,canonical)}
        data['canonical_choice_wording']=canonical
        counts={m:0 for m in scripts}
        before=client.api('GET','/api/program/'+data['session_id'])
        data['initial_program']=before
        module=before.get('runtime',{}).get('current_module','module_1')
        goal=None
        cycle=None
        data['intended']=dict(result=case[-2],decision=case[-1])
        outcome='turn_budget_exhausted'
        for _ in range(26):
            if module not in scripts or counts[module]>=len(scripts[module]):
                outcome='bounded_stall:'+module
                break
            text=scripts[module][counts[module]]
            counts[module]+=1
            row=client.say(data,text)
            if not row['ok']:
                outcome='request_failure'
                break
            rt=row.get('program',{}).get('runtime',{})
            module=rt.get('current_module','unknown')
            goal=goal or rt.get('active_goal_id')
            cycle=cycle or rt.get('active_cycle_id')
            if goal:
                history=client.api('GET',f'/api/program/goals/{goal}/history')
                row['tracked_history']=history
                closed=next((c for c in history.get('cycles',[]) if c.get('id')==cycle and c.get('status')=='completed'),None)
                if closed:
                    outcome='cycle_completed'
                    break
        data['result']=dict(outcome=outcome,attempts=counts,goal_id=goal,cycle_id=cycle,finished_at=datetime.now().isoformat())
        save(path,data)
        print(json.dumps(dict(actor=actor,case=label,RESULT=data['result']),ensure_ascii=False),flush=True)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['init','new','say','batch'])
    parser.add_argument('--actor',default='A')
    parser.add_argument('--label',default='pilot')
    parser.add_argument('--text')
    parser.add_argument('--count',type=int,default=10)
    parser.add_argument('--canonical',action='store_true')
    args=parser.parse_args()
    if args.action=='batch':
        run_actor(args.actor,args.count,args.canonical)
    else:
        c=Client(args.actor)
        if args.action=='new': c.new(args.label)
        elif args.action=='say':
            data=json.loads((c.folder/(args.label+'.json')).read_text(encoding='utf-8'))
            c.say(data,args.text)
