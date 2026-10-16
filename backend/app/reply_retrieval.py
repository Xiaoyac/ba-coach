"""Owned, per-turn retrieval preparation; speculative results require an exact input match."""
import asyncio
import logging
from time import perf_counter
import json
from .retrieval import DatabaseKnowledgeBase
from .retrieval_intent import decide_retrieval
logger = logging.getLogger(__name__)


def knowledge_query(state):
    pieces = [state["user_input"]]
    for message in reversed(state.get("chat_history") or []):
        if message.role != "user" or message.content in pieces:
            continue
        pieces.append(message.content)
        if len(pieces) == 3:
            break
    pa_card = (state.get("memory") or {}).get("pa_card")
    if pa_card:
        pieces.append(pa_card)
    return "\n".join(pieces)[:6000]


def request_key(state, context, module_name, module_config):
    decision = decide_retrieval(state, enabled=context.settings.knowledge_intent_gate_enabled)
    return (module_name, module_config.retrieve, module_config.top_k, knowledge_query(state),
            state.get('subject_id'), state.get('session_id'),
            str(bool((state.get('memory') or {}).get('sandbox_mode'))),
            decision.retrieve, decision.reason, id(context.knowledge_base))


async def _retrieve(state, context, module_name, module_config):
    events = []
    _send = events.append
    retrieval_started = perf_counter()
    knowledge = []
    retrieval_metrics = {}
    if module_config.retrieve:
        gate_started = perf_counter()
        decision = decide_retrieval(
            state, enabled=context.settings.knowledge_intent_gate_enabled
        )
        gate_ms = (perf_counter() - gate_started) * 1000
        retrieval_started = perf_counter()
        outcome = "skipped"
        cache_metrics = {"cache": "bypass_custom_or_skipped"}
        try:
            if decision.retrieve:
                outcome = "error"
                search_args = {"module": module_name, "query": knowledge_query(state), "top_k": module_config.top_k}
                if getattr(context.knowledge_base, "ranking_mode", None) == "astrbot_hybrid":
                    search_args["top_k"] = context.settings.knowledge_hybrid_final_results
                if isinstance(context.knowledge_base, DatabaseKnowledgeBase):
                    subject, session = state.get("subject_id"), state.get("session_id")
                    scope = (subject, session, str(bool((state.get("memory") or {}).get("sandbox_mode")))) if subject and session else None
                    knowledge, cache_metrics = await context.knowledge_base.search_with_diagnostics(
                        **search_args, cache_scope=scope)
                else:
                    knowledge = await context.knowledge_base.search(**search_args)
                outcome = "returned" if knowledge else "empty"
                if cache_metrics.get("status", "").startswith("withheld_"):
                    outcome = "error"
        finally:
            retrieval_ms = (perf_counter() - retrieval_started) * 1000 if decision.retrieve else 0.0
            retrieval_metrics = {
                "gate": decision.as_dict(),
                "gate_duration_ms": round(gate_ms, 4),
                "search_duration_ms": round(retrieval_ms, 4),
                "total_duration_ms": round(gate_ms + retrieval_ms, 4),
                "outcome": outcome,
                "returned": len(knowledge),
                "context_chars": sum(len(chunk.text) for chunk in knowledge),
                "retriever": type(context.knowledge_base).__name__,
                "ranking_mode": getattr(context.knowledge_base, "ranking_mode", "custom"),
                "module": module_name,
                "exact_cache": cache_metrics,
            }
            # Structured server log: no query, transcript, user ID, or reference text.
            logger.info("retrieval_gate %s", json.dumps(retrieval_metrics, ensure_ascii=False))
        _send(
            {
                "type": "trace",
                "node": module_name,
                "detail": {
                    "retrieved": [chunk.id for chunk in knowledge],
                    "top_score": knowledge[0].score if knowledge else None,
                    "score_type": knowledge[0].score_type if knowledge else None,
                    "duration_ms": round(gate_ms + retrieval_ms),
                    "intent_gate": retrieval_metrics,
                },
            }
        )
    kb = context.knowledge_base
    token = (id(kb._index), kb._index_revision, kb._index_epoch) if isinstance(kb, DatabaseKnowledgeBase) else None
    return knowledge, retrieval_metrics, retrieval_started, events, token


async def corpus_is_current(context, token):
    """Check again where prepared evidence is consumed, after graph scheduling."""
    if token is None:
        return True
    kb = context.knowledge_base
    if not isinstance(kb, DatabaseKnowledgeBase):
        return False
    try:
        await kb._load_index()
        return token == (id(kb._index), kb._index_revision, kb._index_epoch)
    except Exception:
        return False


async def route_with_retrieval(state, context, route):
    # Only legacy catalog calls are expensive enough to justify speculation.
    # Risk has already been screened by the graph. No speculative writes,
    # mediator judgments, cross-turn cache or model-generated module guess.
    from .catalog_knowledge_base import CatalogDatabaseKnowledgeBase
    from .graph.nodes import MODULE_CONFIGS, ModuleConfig
    module = state.get('current_module') or state.get('extracted_intent') or 'module_1'
    config = MODULE_CONFIGS.get(module, ModuleConfig())
    context.prepared_retrieval = None
    if (state.get('risk') or state.get('forced_module') or
            (state.get('metadata') or {}).get('goal_card_action') or
            not isinstance(context.knowledge_base, CatalogDatabaseKnowledgeBase) or
            not decide_retrieval(state, enabled=context.settings.knowledge_intent_gate_enabled).retrieve):
        return await route(state, context)
    key = request_key(state, context, module, config)
    task = asyncio.create_task(retrieve(state, context, module, config))
    started = perf_counter()
    try:
        update = await route(state, context)
        fresh = {**state, **update}
        selected = fresh.get('extracted_intent') or module
        matches = key == request_key(fresh, context, selected, MODULE_CONFIGS.get(selected, ModuleConfig()))
        if matches:
            try:
                result = await task
            except Exception:
                # Preserve the module's ordinary error/retry handling.
                result = None
            if result is not None and not await corpus_is_current(context, result[4]):
                result = None
            if result is not None:
                context.prepared_retrieval = (key, result)
        telemetry = dict(update.get('telemetry') or {})
        telemetry['retrieval_overlap'] = {'status':'reused' if context.prepared_retrieval else 'discarded',
            'duration_ms':round((perf_counter()-started)*1000,3)}
        update['telemetry'] = telemetry
        return update
    finally:
        if not task.done():task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def retrieve(state, context, module_name, module_config):
    from .execution_status import track_call, update
    if not module_config.retrieve:
        return await _retrieve(state, context, module_name, module_config)
    result = await track_call('retrieval', _retrieve(state, context, module_name, module_config))
    from .retrieval_timing import publish_timings
    publish_timings(result[1])
    outcome = result[1].get('outcome')
    if outcome == 'skipped':
        update('retrieval', 'skipped')
    elif outcome == 'error':
        update('retrieval', 'failed', detail='检索未成功返回，主回复按降级策略继续')
    return result
