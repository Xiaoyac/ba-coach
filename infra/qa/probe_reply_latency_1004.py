"""Two finite, fully synthetic K3 turns. In-memory sessions and corpus; no DB writes.
Reports timings/counts only. Keep same model, risk gate, lead, full reply and policy.
"""
import asyncio,json,logging
from app.providers.deadline import timeout
from time import perf_counter
from app.config import get_settings
from app.providers import get_provider
from app.session import InMemorySessionStore
from app.graph.state import GraphContext
from app.graph.builder import build_graph
from app.schemas import Message
from app.retrieval import index_chunk
from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.provisional_reply import with_natural_lead
from app import reply_retrieval,pre_reply_routing

async def main():
 logging.disable(logging.CRITICAL)
 settings=get_settings();assert settings.deepseek_model=='kimi-k3'
 provider=get_provider('deepseek');original_overlap=reply_retrieval.route_with_retrieval;original_load=pre_reply_routing.load_routing_snapshot
 async def load(state,ctx):
  snapshot=await original_load(state,ctx);snapshot['routing_mode']='router_only';snapshot['routing_state']['routing_mode']='router_only';return snapshot
 pre_reply_routing.load_routing_snapshot=load
 docs=tuple(index_chunk(id=i,source_id=1,source_name='合成资料',category='BA',heading=title,content=body) for i,title,body in [
  (91001,'渐进活动安排','从符合个人情况的小活动开始，具体安排时间与时长，依据实际执行逐步调整。'),
  (91002,'活动与情绪记录','记录实际活动及情绪变化，有助于发现适合自己的安排。')])
 async def serial(state,ctx,route):ctx.prepared_retrieval=None;return await route(state,ctx)
 for label in ['serial','overlap']:
  reply_retrieval.route_with_retrieval=serial if label=='serial' else original_overlap
  store=InMemorySessionStore(ttl_seconds=300,max_messages=80);session=await store.get_or_create(None)
  await store.set_module(session.session_id,'module_2')
  await store.append(session.session_id,Message(role='user',content='我想找一个下班后可以做的小活动，目前还没有确定。'))
  await store.append(session.session_id,Message(role='assistant',content='可以从你愿意做、负担较小的活动聊起。'))
  kb=CatalogDatabaseKnowledgeBase(provider.with_thinking(False))
  async def corpus():return docs
  kb._load_index=corpus
  ctx=GraphContext(provider=provider.with_deep_reply('low'),router_provider=provider.with_thinking(False),store=store,knowledge_base=kb,settings=settings,stream=True)
  prompt='我想先试试下班后听十分钟音乐，这么小的活动也会有帮助吗？'
  start=perf_counter();state={'session_id':session.session_id,'user_input':prompt,'telemetry':{'reply_mode':'ack_deep'},'turn_started_monotonic':start}
  metrics={};first=None;body=None;complete=None;final={};chars=0
  events=build_graph().compile().astream(state,context=ctx,stream_mode=['custom','values'])
  async with timeout(110):
   async for mode,event in with_natural_lead(events,provider=provider,user_input=prompt,generation_id='synthetic',session_id=session.session_id,subject_id=None,maker=None,metrics=metrics,context=ctx):
    if mode=='values':final=event;continue
    if event.get('type')=='delta' and event.get('text','').strip():
     if first is None:first=round((perf_counter()-start)*1000)
     if event.get('phase')!='lead' and body is None:body=round((perf_counter()-start)*1000)
     chars+=len(event['text'])
    if event.get('type')=='done':complete=round((perf_counter()-start)*1000)
  t=final.get('telemetry') or {};refs=t.get('knowledge_references') or {}
  report={'case':label,'model':provider.model,'effort':'low','routing_mode':final.get('routing_mode'),'first_visible_ms':first,'first_body_ms':body,'body_done_ms':complete,'graph_done_ms':round((perf_counter()-start)*1000),'body_chars':len(final.get('final_response','')),'displayed_chars':chars,'lead_status':metrics.get('status'),'error':bool(final.get('error')),'risk':bool(final.get('risk')),'module':final.get('extracted_intent'),'evidence_ids':[r['id'] for r in refs.get('provided',[])],'retrieval_outcome':refs.get('retrieval_outcome'),'timeline':t.get('execution_timeline'),'overlap':t.get('retrieval_overlap'),'main_usage':final.get('usage')}
  print(json.dumps(report,ensure_ascii=False),flush=True)
 reply_retrieval.route_with_retrieval=original_overlap
if __name__=='__main__':asyncio.run(main())
