from app.retrieval_timing import timing_rows, publish_timings
from app.ai_telemetry import request_event_scope
from app.execution_status import snapshot, _turns
from test_execution_drawer import prepare_api
from test_goal_overview import goal_api
from app.models import AIExecutionEvent
from sqlalchemy import insert
import pytest


def metrics(cache='miss', **details):
    return {'ranking_mode': 'astrbot_hybrid', 'exact_cache': {'cache': cache,
        'retriever_details': {'embedding_duration_ms': 176.594,
            'recall_fusion_duration_ms': 42.17, 'rerank_duration_ms': 7356.897,
            'rerank_model': 'test-model', 'rerank_candidate_count': 20,
            'rerank': 'completed', **details}}}


@pytest.mark.parametrize('cache', ['hit', 'coalesced'])
def test_cached_timings_do_not_replay_original_work(cache):
    rows = timing_rows(metrics(cache))
    assert len(rows) == 3
    assert all(r['status'] == 'skipped' and r['duration_ms'] == 0 for r in rows)


def test_missing_or_invalid_timings_are_not_invented():
    assert timing_rows({}) == []
    assert timing_rows({'ranking_mode':'astrbot_hybrid'}) == []
    rows = timing_rows(metrics(embedding_duration_ms=float('nan'), recall_fusion_duration_ms=-1,
                               rerank='failed'))
    assert len(rows) == 1 and rows[0]['status'] == 'failed'


async def test_live_timings_and_reload_are_separate_and_private(goal_api):
    client, db, _ = await prepare_api(goal_api)
    m = metrics()
    m['exact_cache']['retriever_details']['private_query'] = 'SECRET'
    await db.execute(insert(AIExecutionEvent), {'conversation_id':1,'session_id':'chat-a','subject_id':'a',
        'stage':'main_generation','model_name':'main','duration_ms':100,
        'event_metadata':{'user_message_id':21,'retrieval':m,
            'execution_timeline':[{'stage':'retrieval','status':'empty','duration_ms':7663.4}]}})
    await db.commit()
    async with request_event_scope('chat-a',21):
        publish_timings(m)
        rows = snapshot('chat-a',21)['stages']
        assert rows['query_embedding']['duration_ms'] == 176.594
        assert rows['knowledge_search']['duration_ms'] == 42.17
        assert rows['knowledge_reranker']['duration_ms'] == 7356.897
    _turns.clear()
    response = await client.get('/api/conversations/chat-a/execution')
    assert response.status_code == 200 and 'SECRET' not in response.text
    rows = {r['stage']:r for r in response.json()['stages']}
    assert rows['retrieval']['duration_ms'] == 7663.4
    assert rows['knowledge_search']['duration_ms'] == 42.17
    assert rows['knowledge_reranker']['model'] == 'test-model'
    assert '20 条' in rows['knowledge_reranker']['detail']


async def test_reply_persistence_keeps_retrieval_measurements(db_sessionmaker):
    from app.conversation_store import start_turn, finish_turn
    from sqlalchemy import select
    async with db_sessionmaker() as db:
        user_id = await start_turn(db, subject_id='a', session_id='chat-a', user_text='测试问题')
        await finish_turn(db, session_id='chat-a', user_message_id=user_id,
            reply_text='测试回复', telemetry={'retrieval':metrics()})
        event = await db.scalar(select(AIExecutionEvent).where(AIExecutionEvent.stage=='main_generation'))
        assert event.event_metadata['retrieval']['exact_cache']['retriever_details']['rerank_candidate_count'] == 20
