"""Resolve M2 card operations before the main reply makes operational claims.

Uses the existing tool executor and confirmation contract without changing
clinical gates. The optional conversational lead can still run in parallel.
"""
from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from .prompts import SystemPromptSegment
from .schemas import Message

logger = logging.getLogger(__name__)


async def prepare_pa_reply_node(state, runtime):
    from .pa_card_tools import PACardTools, form_ui_enabled
    context = runtime.context
    module = state.get('extracted_intent') or state.get('current_module')
    if (module != 'module_2' or not form_ui_enabled(context.settings, state)
            or state.get('structured_goal_action_handled') or state.get('risk')
            or state.get('forced_module') or not state.get('user_message_id')
            or not state.get('subject_id') or context.sessionmaker is None):
        return {}

    from .ai_telemetry import save_ai_event, request_event_scope
    from .conversation_time import current_time_context
    from .pa_background import background_provider
    from .pa_background_loop import run_pa_background_tools
    from .prompt_store import effective_prompt_pair
    from .v2_workflow import runtime_for

    telemetry = dict(state.get('telemetry') or {})
    trace = {}
    result = {'status': 'failed', 'reason': 'card_preparation_failed'}
    executor = None
    provider = None
    started = perf_counter()
    async with request_event_scope(state['session_id'], state['user_message_id']):
        try:
            provider = background_provider(context)
            executor = PACardTools(maker=context.sessionmaker, session_id=state['session_id'],
                user_id=state['subject_id'], user_message_id=state['user_message_id'],
                module=module, provider=provider, ui_enabled=True)
            async with context.sessionmaker() as db:
                conversation, _, _ = await executor._owned(db)
                sources = await executor.evidence(db, conversation.id)
                if context.prompt_snapshot is not None:
                    module_prompt = context.prompt_snapshot.get(module, '')
                else:
                    _, module_prompt = await effective_prompt_pair(db, module)
            result = await run_pa_background_tools(provider, executor,
                system=[SystemPromptSegment('你负责核实本轮目标卡操作，沿用以下模块业务规则：\n' + module_prompt),
                    SystemPromptSegment(current_time_context(user_created_at=state.get('user_created_at')),
                        cacheable=False, after_history=True)],
                messages=[Message(role=m.role, content=m.content) for m in sources],
                max_rounds=context.settings.pa_card_tool_max_rounds,
                timeout_seconds=context.settings.pa_card_tool_timeout_seconds, telemetry=trace)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('PA reply preparation failed')

        # Read committed state even after a later tool/provider failure: an
        # earlier successful save must not be reported as rolled back.
        async with context.sessionmaker() as db:
            conversation, fresh = await runtime_for(db, state['session_id'])
            if not conversation or conversation.subject_id != state['subject_id'] or not fresh:
                raise RuntimeError('card_preparation_state_unavailable')
        operation = trace.setdefault('pa_background_tools', {})
        operation.update(result)
        operation['timing'] = 'before_main_reply'
        await save_ai_event(context.sessionmaker, stage='pa_background_tools',
            session_id=state['session_id'], subject_id=state['subject_id'],
            provider=provider.name if provider else None, model_name=provider.model if provider else None,
            duration_ms=int((perf_counter() - started) * 1000), usage=result.get('usage'),
            request_id=result.get('request_id'), error_code=result.get('reason'),
            event_metadata={**trace, 'user_message_id': state['user_message_id']})
        telemetry.update(trace)

    current = fresh['current_module']
    updates = {'pa_operations_completed': True, 'pa_background_pending': False,
        'pa_tools_used': True, 'pa_operation_displays': list(executor.displays) if executor else [],
        'current_module': current, 'extracted_intent': current, 'next_module': current,
        'memory': fresh.get('memory') or {}, 'telemetry': telemetry}
    if executor and any(call.get('result', {}).get('status') == 'confirmed' for call in executor.trace):
        updates['confirmation_receipt'] = {'status': 'confirmed', 'source': 'pa_tools',
            'user_message_id': state['user_message_id']}
    if current != module:
        from .clinical_store import load_clinical_context
        updates['clinical_context'] = await load_clinical_context(context.sessionmaker,
            user_id=state['subject_id'], session_id=state['session_id'])
        updates['knowledge_task'] = 'general'
    return updates
