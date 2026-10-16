"""Finite matched-input catalog measurements. Read-only live corpus, synthetic queries.
No production service mutation, transcript access, or private query output.
"""
import asyncio,json,logging
from time import perf_counter
from app.providers import get_provider
from app.retrieval import get_knowledge_base
from app.db import get_engine
from app.semantic_catalog import SemanticCatalog,PLAN_PROMPT
QUERIES=[
 '我想了解下班以后听一会儿音乐这样的小活动是否有帮助。',
 '最近下班很累，想用听音乐开始试一项很小的活动。',
 '工作结束后我只想休息，听音乐能不能作为一个起点？',
 '我希望从每天听十分钟音乐开始，想知道这样做有什么意义。']
async def main():
 logging.disable(logging.CRITICAL);kb=get_knowledge_base();index=await kb._load_index();catalog=SemanticCatalog(index);p=get_provider('deepseek').with_thinking(False)
 for n,query in enumerate(QUERIES):
  payload=catalog.plan_payload(module='module_2',query=query)
  for variant in (['original','stable_prefix'] if n%2==0 else ['stable_prefix','original']):
   body=payload if variant=='original' else {'module':payload['module'],'sections':payload['sections'],'query_with_recent_context':payload['query_with_recent_context']}
   assert body==payload
   wire=json.dumps(body,ensure_ascii=False);start=perf_counter()
   response=await p.route_detailed(system=PLAN_PROMPT,user=wire,max_tokens=256)
   elapsed=round((perf_counter()-start)*1000,3);ids=[];valid=False
   try:
    ids=json.loads(response.text)['section_ids'];catalog.candidates(module='module_2',query=query,plan_raw=response.text);valid=response.finish_reason=='stop'
   except (ValueError,TypeError,KeyError):pass
   print(json.dumps({'case':n+1,'variant':variant,'model':response.model,'effort':'low','input_chars':len(wire),'duration_ms':elapsed,'valid':valid,'selected_count':len(ids),'selected_ids':ids,'usage':response.usage,'finish_reason':response.finish_reason},ensure_ascii=False),flush=True)
 await get_engine().dispose()
if __name__ == '__main__':asyncio.run(main())
