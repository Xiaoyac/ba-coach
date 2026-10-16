"""Bounded live progress for the single-worker deployment; no prompt contents."""
from __future__ import annotations
import asyncio
from collections import OrderedDict
from contextlib import aclosing
from copy import deepcopy
from time import time
from .execution_errors import exception_info, failure_info, safe_failure

STAGES = {
    'risk_gate': ('风险判断', 'foreground'),
    'module_router': ('Router 路由', 'foreground'),
    'retrieval': ('知识检索（总计）', 'foreground'),
    'query_embedding': ('查询向量化', 'foreground'),
    'knowledge_search': ('本地搜索与合并', 'foreground'),
    'knowledge_reranker': ('知识重排', 'foreground'),
    'knowledge_mediator': ('知识中介', 'foreground'),
    'context_compaction': ('上下文压缩', 'foreground'),
    'main_generation': ('主回复', 'foreground'),
    'reply_lead': ('承接回复', 'parallel'),
    'pa_background_tools': ('目标卡后台', 'parallel'),
    'module_summarizer': ('模块摘要', 'parallel'),
    'clinical_extraction': ('事实提取', 'parallel'),
    'post_reply_persistence': ('对话归档', 'parallel'),
}
_turns = OrderedDict()
TERMINAL = {'completed', 'failed', 'cancelled', 'skipped'}


def _key():
    from .ai_telemetry import _turn_scope
    return _turn_scope.get()


def begin(session_id, user_message_id):
    if user_message_id is None:
        return
    now = time()
    for key, value in list(_turns.items()):
        if now - value['updated_at'] > 7200:
            _turns.pop(key, None)
    key = (session_id, user_message_id)
    _turns[key] = {'started_at': now, 'updated_at': now, 'request_finished': False, 'stages': {}}
    _turns.move_to_end(key)
    while len(_turns) > 512:
        _turns.popitem(last=False)


def update(stage, status, *, model=None, detail=None, duration_ms=None, key=None, failure=None):
    key = key or _key()
    if not key or key[1] is None or stage not in STAGES:
        return
    turn = _turns.get(key)
    if turn is None:
        return  # Recovered history comes from durable events, not invented live runs.
    now = time()
    row = turn['stages'].setdefault(stage, {'stage': stage, 'started_at': now, 'calls': 0})
    if status == 'running' and row.get('status') in TERMINAL:
        row['started_at'] = now
        row.pop('finished_at', None)
    if status == 'running':
        row['calls'] += 1
    previous_status = row.get('status')
    row['status'] = status
    if failure:
        row['failure'] = safe_failure(failure)
    elif status not in {'failed', 'retrying'}:
        row.pop('failure', None)
    elif status == 'failed' and previous_status != 'failed':
        row['failure'] = failure_info()
    if model:
        row['model'] = str(model)
    if detail:
        row['detail'] = str(detail)[:120]
    elif status != 'retrying':
        row.pop('detail', None)
    if status in TERMINAL:
        row['finished_at'] = now
        row['duration_ms'] = duration_ms if duration_ms is not None else round((now-row['started_at'])*1000)
    turn['updated_at'] = now


def end_request(session_id, user_message_id):
    turn = _turns.get((session_id, user_message_id))
    if turn is None:
        return
    turn['request_finished'] = True
    for stage, row in list(turn['stages'].items()):
        if STAGES[stage][1] == 'foreground' and row['status'] not in TERMINAL:
            update(stage, 'cancelled', detail='请求已结束，未收到阶段完成记录', key=(session_id, user_message_id))
    turn['updated_at'] = time()


def snapshot(session_id, user_message_id):
    value = _turns.get((session_id, user_message_id))
    return deepcopy(value) if value else None


async def track_call(stage, awaitable, *, model=None):
    update(stage, 'running', model=model)
    try:
        result = await awaitable
    except asyncio.CancelledError:
        update(stage, 'cancelled')
        raise
    except Exception as exc:
        update(stage, 'failed', failure=exception_info(exc))
        raise
    update(stage, 'completed', model=getattr(result, 'model', None))
    return result


async def track_stream(stage, iterator, *, model=None):
    update(stage, 'running', model=model)
    try:
        async with aclosing(iterator):
            async for value in iterator:
                yield value
    except (asyncio.CancelledError, GeneratorExit):
        update(stage, 'cancelled')
        raise
    except Exception as exc:
        update(stage, 'failed', failure=exception_info(exc))
        raise
    else:
        update(stage, 'completed')


def event_status(stage, metadata, error=None):
    meta = metadata or {}
    state = ((meta.get('pa_background_tools') or {}).get('status')
             if stage == 'pa_background_tools' else meta.get('status'))
    if error in {'client_disconnected', 'generation_cancelled', 'superseded'} or state == 'cancelled':
        return 'cancelled'
    if error or state in {'failed', 'stale', 'error', 'fallback'}:
        return 'failed'
    if state in {'skipped', 'bypassed', 'disabled'}:
        return 'skipped'
    return 'completed'


def record_event(stage, *, session_id, metadata, model=None, error=None, duration_ms=None):
    key = (session_id, (metadata or {}).get('user_message_id'))
    if stage not in STAGES or key[1] is None:
        return
    status = event_status(stage, metadata, error)
    existing = (snapshot(*key) or {}).get('stages', {}).get(stage, {})
    failure = safe_failure((metadata or {}).get('execution_failure')) or existing.get('failure')
    if status == 'failed' and (not failure or (failure.get('code') == 'unknown' and error)):
        failure = failure_info(error)
    update(stage, status, model=model, duration_ms=duration_ms, key=key,
           failure=failure if status == 'failed' else None)


def routing_model(provider):
    settings = getattr(provider, '_settings', None)
    return getattr(settings, provider.name + '_router_model', None) or provider.model
