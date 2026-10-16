"""Synthetic M3/M4 acceptance with effective global and reviewed module prompts.

No production conversations or application writes. Provider calls use the
ordinary-user reply effort. JSONL contains synthetic visible replies/usage only.
"""
import asyncio
import hashlib
import json
import sys
import os
from pathlib import Path
from time import perf_counter
from sqlalchemy import select, text
from app.db import get_sessionmaker, dispose_db
from app.models import PromptOverride
from app.prompts import GLOBAL_PROMPT, build_system_segments
from app.context_pipeline import prepare_context
from app.providers import get_provider
from app.schemas import Message

async def main():
    pack=json.loads(Path(sys.argv[1]).read_text())
    async with get_sessionmaker()() as db:
        if db.bind.dialect.name == 'mysql':
            await db.execute(text('SET TRANSACTION READ ONLY'))
        row=(await db.execute(select(PromptOverride).where(PromptOverride.prompt_key=='global'))).scalar_one_or_none()
        global_prompt=row.content if row else GLOBAL_PROMPT
    provider=get_provider().with_deep_reply('low')
    cases=[]
    for status,quote in [('accepted','我愿意用网页记录'),('declined','我不想填写网页记录，做完直接和你说'),('unknown','之前没谈完怎么记录')]:
        for choice in ['keep','change']:
            activity='散步十分钟' if choice=='keep' else '拉伸五分钟'
            cases.append(dict(name=f'm3_{status}_{choice}',module='module_3',
                facts=f'已提交业务状态：current_module=module_3，cycle_id=synthetic-new-cycle，当前有效确认计划={activity}，明晚七点；本次确认已提交成功。新一轮首次进入M3。上一轮已完成复盘。历史用户表达（不是本轮确认）：{quote}。当前周期recording_status=unknown。',
                turns=[('user','上一轮复盘完了，这次'+('继续散步十分钟' if choice=='keep' else '换成拉伸五分钟')),
                       ('assistant',f'这轮安排是明晚七点{activity}，你看可以吗？')],
                user='确认这个安排',followup='好的'))
    for label,user in [('mixed','跑步半小时没做，加班很晚，已经不打算补了。不过回家跳绳十分钟，心情也没变。'),
                       ('temporary','跑步半小时完成了，之后感觉挺轻松的。我还临时帮室友搬了十分钟东西，没有提前设过这个目标。'),
                       ('old','我说的是上周的旧站桩目标，昨天完成了站桩。新的跑步还没做。')]:
        cases.append(dict(name='m4_'+label,module='module_4',facts='当前已确认核心计划：今晚跑步半小时。当前周期已绑定该跑步计划。历史有已结束的站桩目标。对旧目标的补充只追加事实，不重开旧复盘。',turns=[('assistant','这次活动实际怎么样？')],user=user))
    for label,result in [('done','散步十分钟完成，放松了一些'),('not_done','执行窗口已经过去，最终没有出门'),('unsuccessful','跑步尝试完成，但没有达到预想感受')]:
        cases.append(dict(name='m4_close_'+label,module='module_4',facts=f'当前本轮结果：{result}。ABC已由用户核对，相关BA解释用户已理解，真实困难及应对已讨论完毕；尚未总结。下一轮目标未决定。',turns=[('assistant','这次的困难和应对我们聊清楚了，还有需要修正的地方吗？')],user='没有了，你总结一下吧'))
    selected = set(filter(None, os.environ.get('CASE_NAMES', '').split(',')))
    if selected:
        cases = [case for case in cases if case['name'] in selected]
    sem=asyncio.Semaphore(2)
    async def run(case):
        async with sem:
            history=[Message(role=r,content=c) for r,c in case['turns']]
            user=case['user']
            for step in range(2 if case.get('followup') else 1):
                start=perf_counter()
                try:
                    system=build_system_segments(case['module'],global_prompt=global_prompt,module_prompt=pack[case['module']]['content'],clinical_context=[case['facts']],history=history)
                    prepared=prepare_context(system=system,history=history,user_input=user,max_history_messages=80)
                    result=await provider.complete(system=prepared.system,messages=prepared.messages)
                    print(json.dumps(dict(case=case['name'],step=step,text=result.text,request_id=result.request_id,model=result.model,usage=result.usage,elapsed_ms=round((perf_counter()-start)*1000),prompt_hash=hashlib.sha256(pack[case['module']]['content'].encode()).hexdigest()),ensure_ascii=False),flush=True)
                    history += [Message(role='user',content=user),Message(role='assistant',content=result.text)]
                    user=case.get('followup','')
                except Exception as exc:
                    print(json.dumps(dict(case=case['name'],step=step,error=type(exc).__name__,detail=str(exc)[:500]),ensure_ascii=False),flush=True)
                    break
    await asyncio.gather(*(run(case) for case in cases))
    await dispose_db()

if __name__=='__main__':asyncio.run(main())
