import asyncio
from time import perf_counter
from types import SimpleNamespace
import pytest
from app.catalog_knowledge_base import CatalogDatabaseKnowledgeBase
from app.config import Settings
from app.graph.nodes import MODULE_CONFIGS
from app.reply_retrieval import request_key, route_with_retrieval
from app import reply_retrieval as m


def context():
    return SimpleNamespace(settings=Settings(), knowledge_base=CatalogDatabaseKnowledgeBase(object()))

def state():
    return {'current_module':'module_2','extracted_intent':'module_2','user_input':'想了解怎么开始活动',
            'subject_id':'synthetic','session_id':'owned','chat_history':[], 'memory':{}, 'telemetry':{}}

@pytest.mark.asyncio
async def test_retrieval_and_router_overlap_without_changing_evidence(monkeypatch):
    entered=asyncio.Event();release=asyncio.Event();evidence=([object()],{'outcome':'returned'},1,[],None)
    async def fetch(*args):entered.set();await release.wait();return evidence
    async def router(s,c):await asyncio.wait_for(entered.wait(),1);release.set();return {'extracted_intent':'module_2'}
    monkeypatch.setattr(m,'retrieve',fetch);c=context();s=state()
    result=await route_with_retrieval(s,c,router)
    assert c.prepared_retrieval==(request_key(s,c,'module_2',MODULE_CONFIGS['module_2']),evidence)
    assert result['telemetry']['retrieval_overlap']['status']=='reused'

@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'extracted_intent':'module_3'},{'memory':{'pa_card':'changed'}},
                                  {'subject_id':'other'},{'session_id':'other'},{'user_input':'different'},
                                  {'user_input':'你好'}])
async def test_changed_request_discards_and_cancels_without_waiting(monkeypatch,change):
    entered=asyncio.Event();cancelled=asyncio.Event()
    async def fetch(*args):
        entered.set()
        try:await asyncio.Future()
        finally:cancelled.set()
    async def router(s,c):await entered.wait();return change
    monkeypatch.setattr(m,'retrieve',fetch);c=context()
    result=await asyncio.wait_for(route_with_retrieval(state(),c,router),1)
    assert c.prepared_retrieval is None and cancelled.is_set()
    assert result['telemetry']['retrieval_overlap']['status']=='discarded'

@pytest.mark.asyncio
@pytest.mark.parametrize('blocked',[{'risk':{'risk_status':True}},{'forced_module':'module_2'},
                                     {'metadata':{'goal_card_action':'confirm'}},{'user_input':'你好'}])
async def test_no_speculation_for_risk_actions_forced_or_skipped(monkeypatch,blocked):
    async def fetch(*args):raise AssertionError('speculative call forbidden')
    async def router(s,c):return {'extracted_intent':'module_2'}
    monkeypatch.setattr(m,'retrieve',fetch);c=context()
    await route_with_retrieval({**state(),**blocked},c,router)
    assert c.prepared_retrieval is None

@pytest.mark.asyncio
@pytest.mark.parametrize('cancel',[False,True])
async def test_route_error_or_cancellation_cleans_up(monkeypatch,cancel):
    entered=asyncio.Event();clean=asyncio.Event()
    async def fetch(*args):
        entered.set()
        try:await asyncio.Future()
        finally:clean.set()
    async def router(s,c):await entered.wait();raise asyncio.CancelledError() if cancel else RuntimeError('failed')
    monkeypatch.setattr(m,'retrieve',fetch)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):await route_with_retrieval(state(),context(),router)
    assert clean.is_set()

@pytest.mark.asyncio
async def test_prefetch_failure_leaves_normal_retrieval_available(monkeypatch):
    async def fetch(*args):raise ValueError('search failed')
    async def router(s,c):await asyncio.sleep(0);return {'extracted_intent':'module_2'}
    monkeypatch.setattr(m,'retrieve',fetch);c=context()
    await route_with_retrieval(state(),c,router)
    assert c.prepared_retrieval is None

@pytest.mark.asyncio
async def test_same_delays_comparison(monkeypatch):
    async def fetch(*args):await asyncio.sleep(.24);return ([],{},perf_counter(),[],None)
    async def route(*args):await asyncio.sleep(.16);return {'extracted_intent':'module_2'}
    monkeypatch.setattr(m,'retrieve',fetch)
    start=perf_counter();await route();await fetch();serial=perf_counter()-start
    start=perf_counter();await route_with_retrieval(state(),context(),route);parallel=perf_counter()-start
    assert parallel < serial*.85
    print(f'SAME_INPUT_ROUTER_RETRIEVAL serial_ms={serial*1000:.1f} parallel_ms={parallel*1000:.1f}')

@pytest.mark.asyncio
async def test_corpus_revision_change_discards_completed_prefetch(monkeypatch):
    c=context();ready=asyncio.Event()
    async def fetch(*args):
        token=(id(c.knowledge_base._index),c.knowledge_base._index_revision,c.knowledge_base._index_epoch)
        ready.set();return ([],{},perf_counter(),[],token)
    async def route(*args):
        await ready.wait();c.knowledge_base.invalidate('synthetic corpus update');return {'extracted_intent':'module_2'}
    async def load():return ()
    c.knowledge_base._load_index=load;monkeypatch.setattr(m,'retrieve',fetch)
    result=await route_with_retrieval(state(),c,route)
    assert result['telemetry']['retrieval_overlap']['status']=='discarded'
    assert c.prepared_retrieval is None
