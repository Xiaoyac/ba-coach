"""Detached, source-bound PA tool work using the background LLM only."""
from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from .providers.base import ProviderError

logger = logging.getLogger(__name__)
_pa_tasks: dict[str, asyncio.Task] = {}

FOREGROUND_POLICY = """本轮你只负责自然回应用户，PA 卡片的查询、保存、修改、确认和收尾由独立后台处理。
不要调用或模拟工具，不输出工具名、JSON、操作过程或声称本轮卡片已保存、已确认、已删除或已结束。
依据已经提供的数据库事实和用户真实发言继续交流；未完成的卡片处理不应变成让用户重发、重复确认或等待的要求。
需要展示的正式卡片由系统在后台完成后自动补入对话；不要手写一份冒充已经保存的正式卡片。
对本轮确认可以自然接住用户的决定，但不能在后台完成前宣称提交成功。"""


def background_provider(context):
    """A separate wrapper, pinned to the configured background/router model."""
    provider = context.router_provider.with_thinking(False)
    if not provider.supports_native_tools:
        raise ProviderError('The configured background model does not support PA tools')
    settings = context.settings
    provider.model = getattr(settings, provider.name + '_router_model', provider.model)
    provider.tool_timeout_seconds = settings.background_model_timeout_seconds
    provider.tool_max_tokens = settings.pa_card_background_max_tokens
    return provider


def schedule_pa_background(state, context, *, assistant_message_id, parallel=False):
    """Start once per turn; neither this nor the next reply waits for PA."""
    existing = getattr(context, 'pa_background_task', None)
    if existing is not None and getattr(context, 'pa_background_turn', None) == state.get('user_message_id'):
        return existing
    if (not state.get('pa_background_pending') or state.get('structured_goal_action_handled')
            or state.get('error') or state.get('reply_held')
            or state.get('risk') or state.get('forced_module')
            or (state.get('memory') or {}).get('sandbox_mode')
            or context.settings.database_schema_version != 'v2' or context.sessionmaker is None
            or not state.get('subject_id') or not state.get('user_message_id')
            or (assistant_message_id is None and not parallel)):
        return None
    session_id = state['session_id']
    previous = _pa_tasks.get(session_id)
    if previous is not None and not previous.done():
        previous.cancel()
    task = asyncio.create_task(_run_pa_background(dict(state), context,
        assistant_message_id=assistant_message_id, parallel=parallel))
    _pa_tasks[session_id] = task
    context.pa_background_task = task
    context.pa_background_turn = state['user_message_id']
    stop_waiter = None
    if parallel and context.generation is not None:
        async def stop_with_turn():
            await context.generation.stop.wait()
            task.cancel()
        stop_waiter = asyncio.create_task(stop_with_turn())

    def remove(completed):
        if stop_waiter is not None:
            stop_waiter.cancel()
        if _pa_tasks.get(session_id) is completed:
            _pa_tasks.pop(session_id, None)

    task.add_done_callback(remove)
    return task


async def shutdown_pa_background():
    """Finish cancellation before the application's database pool is closed."""
    tasks = list(_pa_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _publish(executor, context, telemetry, expected_reply=None):
    """Publish committed artifacts; no new model request and no invented receipt."""
    from .models import ConversationMessage
    from .schemas import Message
    from .pa_card_tools import finalize_tool_display
    from .goal_card_interaction import finalize_ui_display
    from .v2_workflow import runtime_for

    lock = await context.store.get_turn_lock(executor.session_id)
    async with lock:
        async with context.sessionmaker() as db:
            conversation, _, _ = await executor._owned(db)
            assistant = (await db.get(ConversationMessage, executor.assistant_message_id)
                         if executor.assistant_message_id is not None else None)
            if assistant is not None:
                # A parallel worker can append a display after persistence but
                # before publishing. Match the in-memory prose, not that newer
                # DB text, when synchronizing the owned last reply.
                live = await context.store.get(executor.session_id)
                last = live.messages[-1] if live and live.messages else None
                expected = expected_reply or (last if last and last.role == 'assistant'
                    and last.created_at == assistant.created_at else
                    Message(role='assistant', content=assistant.content, created_at=assistant.created_at))
                for display in executor.displays:
                    if display not in assistant.content:
                        assistant.content += ('\n\n' if assistant.content else '') + display
                await db.flush()
                await finalize_tool_display(db, session_id=executor.session_id, user_id=executor.user_id,
                    assistant_message_id=assistant.id)
                if executor.ui_enabled:
                    await finalize_ui_display(db, session_id=executor.session_id, user_id=executor.user_id,
                        assistant_message_id=assistant.id)
            _, fresh = await runtime_for(db, executor.session_id)
            content = assistant.content if assistant is not None else None
            conversation.revision += 1
            await db.commit()
        if content is not None:
            await context.store.replace_last_reply(executor.session_id, expected=expected, content=content)
        await context.store.set_module(executor.session_id, fresh['current_module'])
        await context.store.set_memory(executor.session_id, fresh['memory'] or {})


async def _run_pa_background(state, context, *, assistant_message_id, parallel=False):
    from .ai_telemetry import save_ai_event, request_event_scope
    from .conversation_time import current_time_context
    from .pa_card_tools import PACardTools, ToolRejected, form_ui_enabled
    from .pa_background_loop import run_pa_background_tools
    from .prompt_store import effective_prompt_pair
    from .prompts import SystemPromptSegment
    from .schemas import Message

    session_id, subject_id = state['session_id'], state['subject_id']
    module = state.get('extracted_intent') or state.get('current_module')
    telemetry = {}
    result = {'status': 'failed', 'reason': 'not_started'}
    provider = None
    executor = None
    started = perf_counter()
    tools_finished = None
    expected_reply = None
    async with request_event_scope(session_id, state['user_message_id']):
        try:
            provider = background_provider(context)
            from .execution_status import update as progress
            progress("pa_background_tools", "running", model=provider.model)
            executor = PACardTools(maker=context.sessionmaker, session_id=session_id, user_id=subject_id,
                user_message_id=state['user_message_id'], assistant_message_id=assistant_message_id,
                module=module, provider=provider, ui_enabled=form_ui_enabled(context.settings, state),
                parallel_reply=parallel)
            async with context.sessionmaker() as db:
                conversation, persisted, _ = await executor._owned(db)
                if persisted['current_module'] != module:
                    raise ToolRejected('module_changed_requery_state')
                sources = await executor.evidence(db, conversation.id)
                from .models import ConversationMessage
                if executor.assistant_message_id is not None:
                    assistant = await db.get(ConversationMessage, executor.assistant_message_id)
                    expected_reply = Message(role='assistant', content=assistant.content, created_at=assistant.created_at)
                if context.prompt_snapshot is not None:
                    module_prompt = context.prompt_snapshot.get(module, '')
                else:
                    _, module_prompt = await effective_prompt_pair(db, module)
            system = [SystemPromptSegment('你是后台 PA 卡片操作员。按以下模块业务规则整理真实对话证据，并通过提供的工具处理卡片。\n' + module_prompt),
                SystemPromptSegment(current_time_context(user_created_at=state.get('user_created_at')),
                    cacheable=False, after_history=True)]
            result = await run_pa_background_tools(provider, executor, system=system,
                messages=[Message(role=m.role, content=m.content) for m in sources],
                max_rounds=context.settings.pa_card_tool_max_rounds,
                timeout_seconds=context.settings.pa_card_tool_timeout_seconds, telemetry=telemetry)
            tools_finished = perf_counter()
            progress("pa_background_tools", "publishing", detail="工具处理结束，等待绑定本轮回复")
            telemetry.setdefault('pa_background_tools', {}).update(provider=provider.name, model=provider.model)
            # Completed writes survive later LLM failure, but superseded turns
            # cannot alter the transcript or overwrite a newer runtime cache.
            await _publish(executor, context, telemetry, expected_reply)
        except asyncio.CancelledError:
            result = {'status': 'cancelled', 'reason': 'superseded'}
            raise
        except ToolRejected as exc:
            result = {'status': 'stale', 'reason': str(exc)}
        except Exception as exc:
            from .execution_errors import exception_info
            telemetry['execution_failure'] = exception_info(exc)
            result = {'status': 'failed', 'reason': 'background_pa_failed'}
            logger.exception('background PA tools failed session=%s', session_id)
        finally:
            finished = tools_finished or perf_counter()
            operation = telemetry.setdefault('pa_background_tools', {})
            operation.update(result)
            operation.update(timing='parallel_with_main_reply' if parallel else 'after_main_reply',
                start_ms=round((started - state.get('turn_started_monotonic', started)) * 1000, 3),
                tools_duration_ms=round((finished - started) * 1000, 3),
                publish_wait_ms=round((perf_counter() - finished) * 1000, 3) if tools_finished else 0)
            telemetry['user_message_id'] = state['user_message_id']
            try:
                await save_ai_event(context.sessionmaker, stage='pa_background_tools', session_id=session_id,
                    subject_id=subject_id, assistant_message_id=executor.assistant_message_id if executor is not None else assistant_message_id,
                    provider=provider.name if provider else None, model_name=provider.model if provider else None,
                    duration_ms=int((finished - started) * 1000), usage=result.get('usage'),
                    request_id=result.get('request_id'), error_code=result.get('reason'), event_metadata=telemetry)
            except Exception:
                logger.exception('background PA telemetry persistence failed session=%s', session_id)
    return result
