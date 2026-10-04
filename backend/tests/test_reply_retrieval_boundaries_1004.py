"""Deterministic synthetic scheduling; no DB, network, or real providers."""
import asyncio
from time import perf_counter
from types import SimpleNamespace
import pytest
from app import reply_retrieval as m
from app.config import Settings
from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.graph.nodes import MODULE_CONFIGS,MODULE_NODES
from app.graph import nodes
from app.graph.state import GraphContext
from app.retrieval import KnowledgeChunk,StubKnowledgeBase
from app.schemas import Message

def ctx():
 kb=CatalogDatabaseKnowledgeBase(object());kb._index=()
 async def load():return kb._index
 kb._load_index=load
 return GraphContext(provider=object(),router_provider=object(),store=object(),knowledge_base=kb,settings=Settings(_env_file=None),prompt_snapshot={})
def state(query='活动如何帮助心情'):
 return dict(current_module='module_2',extracted_intent='module_2',user_input=query,subject_id='synthetic',session_id='owned',chat_history=[],memory={})
def result(c,label='old'):
 kb=c.knowledge_base
 return ([KnowledgeChunk('kb:1',label,'synthetic')],{'outcome':'returned'},perf_counter(),[],(id(kb._index),kb._index_revision,kb._index_epoch))

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['history','sandbox','kb_instance','gate','top_k'])
@pytest.mark.parametrize('prefetch_finishes_first',[False,True])
async def test_changes_discard_both_pending_and_finished(monkeypatch,change,prefetch_finishes_first):
 c=ctx();s=state();ready=asyncio.Event();clean=asyncio.Event()
 async def fetch(*args):
  ready.set()
  try:
   if not prefetch_finishes_first:await asyncio.Future()
   return result(c)
  finally:clean.set()
 async def route(*args):
  await ready.wait()
  if change=='history':return {'chat_history':[Message(role='user',content='改成游泳而非散步')]}
  if change=='sandbox':return {'memory':{'sandbox_mode':True}}
  if change=='kb_instance':c.knowledge_base=ctx().knowledge_base
  if change=='gate':c.settings=c.settings.model_copy(update={'knowledge_intent_gate_enabled':False})
  if change=='top_k':
   from dataclasses import replace
   monkeypatch.setitem(MODULE_CONFIGS,'module_2',replace(MODULE_CONFIGS['module_2'],top_k=1))
  return {'extracted_intent':'module_2'}
 monkeypatch.setattr(m,'retrieve',fetch)
 update=await asyncio.wait_for(m.route_with_retrieval(s,c,route),1)
 assert c.prepared_retrieval is None and clean.is_set()
 assert update['telemetry']['retrieval_overlap']['status']=='discarded'

@pytest.mark.asyncio
@pytest.mark.parametrize('query',['活动如何帮助心情','我不想跳绳，只想散步','站姿桨板每分钟40到49划对应哪条资料？'])
@pytest.mark.parametrize('first',['router','retrieval'])
async def test_scheduler_order_never_changes_evidence(monkeypatch,query,first):
 c=ctx();s=state(query);expected=result(c);ready=asyncio.Event();release=asyncio.Event()
 async def fetch(*args):
  ready.set()
  if first=='router':await release.wait()
  return expected
 async def route(*args):await ready.wait();release.set();return {'extracted_intent':'module_2'}
 monkeypatch.setattr(m,'retrieve',fetch)
 await m.route_with_retrieval(s,c,route)
 assert c.prepared_retrieval[1] is expected

@pytest.mark.asyncio
async def test_caller_cancel_after_router_before_retrieval_finishes(monkeypatch):
 c=ctx();entered=asyncio.Event();routed=asyncio.Event();clean=asyncio.Event()
 async def fetch(*args):
  entered.set()
  try:await asyncio.Future()
  finally:clean.set()
 async def route(*args):await entered.wait();routed.set();return {'extracted_intent':'module_2'}
 monkeypatch.setattr(m,'retrieve',fetch)
 t=asyncio.create_task(m.route_with_retrieval(state(),c,route));await routed.wait();t.cancel()
 with pytest.raises(asyncio.CancelledError):await t
 assert clean.is_set() and c.prepared_retrieval is None

@pytest.mark.asyncio
async def test_revision_refresh_failure_discards_prefetch(monkeypatch):
 c=ctx()
 async def fetch(*args):return result(c)
 async def route(*args):return {'extracted_intent':'module_2'}
 async def broken():raise OSError('synthetic revision unavailable')
 monkeypatch.setattr(m,'retrieve',fetch);c.knowledge_base._load_index=broken
 await m.route_with_retrieval(state(),c,route)
 assert c.prepared_retrieval is None

@pytest.mark.asyncio
async def test_non_catalog_does_not_speculate(monkeypatch):
 c=ctx();c.knowledge_base=StubKnowledgeBase()
 async def forbidden(*args):pytest.fail('noncatalog speculative read')
 async def route(*args):return {'extracted_intent':'module_2'}
 monkeypatch.setattr(m,'retrieve',forbidden)
 await m.route_with_retrieval(state(),c,route)
 assert c.prepared_retrieval is None

class ObservedMediator(BaseException):pass
@pytest.mark.asyncio
@pytest.mark.parametrize('change_after_route',[False,True])
async def test_corpus_change_between_router_and_module_must_not_reuse_stale(monkeypatch,change_after_route):
 c=ctx();s=state();seen=[];calls=[]
 async def prefetch(*args):return result(c,'old')
 async def route(*args):return {'extracted_intent':'module_2'}
 monkeypatch.setattr(m,'retrieve',prefetch)
 await m.route_with_retrieval(s,c,route)
 assert c.prepared_retrieval is not None
 if change_after_route:c.knowledge_base.invalidate('synthetic update in graph transition')
 async def fresh(*args):calls.append(1);return result(c,'new')
 async def observe(**kwargs):seen.extend(k.text for k in kwargs['knowledge']);raise ObservedMediator
 monkeypatch.setattr(m,'retrieve',fresh);monkeypatch.setattr(nodes,'mediate_knowledge',observe)
 with pytest.raises(ObservedMediator):await MODULE_NODES['module_2'](s,SimpleNamespace(context=c),writer=lambda event:None)
 assert seen==(['new'] if change_after_route else ['old'])
 assert len(calls)==int(change_after_route)
 assert c.prepared_retrieval is None

@pytest.mark.asyncio
@pytest.mark.parametrize('late_change',['revision','index','load_error'])
async def test_consumer_refresh_catches_external_changes_or_lookup_failure(monkeypatch,late_change):
 c=ctx();s=state();seen=[];loads=0
 async def fetch(*args):return result(c,'old')
 async def route(*args):return {'extracted_intent':'module_2'}
 monkeypatch.setattr(m,'retrieve',fetch)
 await m.route_with_retrieval(s,c,route)
 async def late_load():
  nonlocal loads
  loads+=1
  if late_change=='revision':c.knowledge_base._index_revision='changed by another process'
  elif late_change=='index':c.knowledge_base._index=(object(),)
  else:raise OSError('synthetic unavailable revision')
  return c.knowledge_base._index
 c.knowledge_base._load_index=late_load
 async def fresh(*args):return result(c,'new')
 async def observe(**kw):seen.extend(h.text for h in kw['knowledge']);raise ObservedMediator
 monkeypatch.setattr(m,'retrieve',fresh);monkeypatch.setattr(nodes,'mediate_knowledge',observe)
 with pytest.raises(ObservedMediator):await MODULE_NODES['module_2'](s,SimpleNamespace(context=c),writer=lambda e:None)
 assert loads==1 and seen==['new'] and c.prepared_retrieval is None

@pytest.mark.asyncio
async def test_cancellation_during_consumer_revision_lookup_never_uses_old(monkeypatch):
 c=ctx();s=state();checking=asyncio.Event();clean=asyncio.Event()
 async def fetch(*args):return result(c,'old')
 async def route(*args):return {'extracted_intent':'module_2'}
 monkeypatch.setattr(m,'retrieve',fetch)
 await m.route_with_retrieval(s,c,route)
 async def lookup():
  checking.set()
  try:await asyncio.Future()
  finally:clean.set()
 c.knowledge_base._load_index=lookup
 async def forbidden(**kw):pytest.fail('cancelled reply reached mediator')
 monkeypatch.setattr(nodes,'mediate_knowledge',forbidden)
 task=asyncio.create_task(MODULE_NODES['module_2'](s,SimpleNamespace(context=c),writer=lambda e:None))
 await asyncio.wait_for(checking.wait(),1);task.cancel()
 with pytest.raises(asyncio.CancelledError):await task
 assert clean.is_set() and c.prepared_retrieval is None

@pytest.mark.asyncio
async def test_concurrent_turns_do_not_share_prepared_evidence(monkeypatch):
 owners=[ctx() for _ in range(20)]
 async def fetch(s,c,*args):await asyncio.sleep(0);return result(c,s['session_id'])
 async def route(s,c):await asyncio.sleep(0);return {'extracted_intent':'module_2'}
 monkeypatch.setattr(m,'retrieve',fetch)
 await asyncio.gather(*(m.route_with_retrieval({**state(),'session_id':str(i)},c,route) for i,c in enumerate(owners)))
 assert [c.prepared_retrieval[1][0][0].text for c in owners]==[str(i) for i in range(20)]
