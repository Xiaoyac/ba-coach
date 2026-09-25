"""/api/chat — HTTP surface over the LangGraph workflow.

This module owns transport concerns only: validate the request, build the
graph's initial state and runtime context, run the graph, and translate what
comes back into JSON or SSE frames. All orchestration lives in `app.graph`.

    POST /api/chat          -> ainvoke, full reply as JSON
    POST /api/chat/stream   -> astream(stream_mode="custom"), SSE deltas

Omit `session_id` on the first turn; the server returns the one it minted.
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_telemetry import add_ai_event
from ..config import get_settings
from ..conversation_store import finish_turn, save_runtime_state, start_turn
from ..db import get_db, get_sessionmaker
from ..graph import (
    AgentState,
    GraphContext,
    get_graph,
    schedule_background_routing,
    wait_for_pending_routing,
)
from ..identity import optional_subject_id, require_subject_id
from ..generation_control import GenerationControl, GenerationStopped, generations
from ..models import (
    AccountSettings,
    Conversation,
    ConversationRuntimeState,
    UserAccount,
)
from ..memos_integration import get_memos_manager
from ..prompt_store import effective_router_prompt
from ..prompts import MODULE_PROMPTS
from ..providers import LLMProvider, ProviderError, get_provider
from ..reasoning import normalize_reasoning_channels
from ..retrieval import get_knowledge_base
from ..schemas import CancelGenerationRequest, ChatRequest, ChatResponse, Message, SessionInfo
from ..session import SessionStore, get_session_store

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])

# Graph events that are forwarded to SSE clients. `trace` is intentionally
# excluded by default — it is node-level telemetry, useful in logs and in the
# debug endpoint, but noise on a user-facing stream.
CLIENT_EVENTS = {
    "meta",
    "reasoning_delta",
    "delta",
    "routing_reasoning",
    "done",
    "error",
}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _persist_user_message(
    db: AsyncSession,
    *,
    subject_id: str | None,
    session_id: str,
    user_text: str,
) -> int | None:
    """Best-effort immediate write of the user's message.

    An unauthenticated caller (curl, /docs, an older client) has no
    subject to key the row on, so it is silently skipped rather than
    rejected — chat itself must keep working either way. A DB failure here
    must not stop the model response; it is logged and the live session still
    proceeds even if durable cross-device sync is temporarily unavailable.
    """
    if not subject_id:
        return None
    try:
        return await start_turn(
            db,
            subject_id=subject_id,
            session_id=session_id,
            user_text=user_text,
        )
    except Exception:  # noqa: BLE001
        await db.rollback()
        logger.exception("failed to persist user message for session %s", session_id)
        return None


async def _persist_assistant_message(
    db: AsyncSession,
    *,
    subject_id: str | None,
    session_id: str,
    user_message_id: int | None,
    reply_text: str,
    reasoning_content: str = "",
    model_name: str | None = None,
    provider_name: str | None = None,
    routing_reasoning_content: str = "",
    router_model_name: str | None = None,
    telemetry: dict | None = None,
) -> int | None:
    """Best-effort write of the reply after model generation finishes."""
    if not subject_id or user_message_id is None:
        return None
    normalized = normalize_reasoning_channels(reply_text, reasoning_content)
    reply_text = normalized.reply
    reasoning_content = normalized.reasoning
    if not reply_text:
        metrics = telemetry or {}
        await add_ai_event(
            db,
            stage="main_generation",
            session_id=session_id,
            subject_id=subject_id,
            provider=provider_name,
            model_name=model_name,
            duration_ms=metrics.get("main_generation_duration_ms"),
            usage=metrics.get("usage") or {},
            request_id=metrics.get("provider_request_id"),
            finish_reason=metrics.get("finish_reason"),
            error_code=metrics.get("error_code") or "empty_reply",
            prompt_version=metrics.get("prompt_version"),
        )
        await db.commit()
        return None
    try:
        return await finish_turn(
            db,
            session_id=session_id,
            user_message_id=user_message_id,
            reply_text=reply_text,
            reasoning_content=reasoning_content,
            model_name=model_name,
            provider_name=provider_name,
            routing_reasoning_content=routing_reasoning_content,
            router_model_name=router_model_name,
            telemetry=telemetry,
        )
    except Exception:  # noqa: BLE001
        await db.rollback()
        logger.exception("failed to persist assistant message for session %s", session_id)
        return None


async def _resolve_provider(
    request: ChatRequest,
    db: AsyncSession,
    *,
    subject_id: str | None,
) -> LLMProvider:
    selected = request.provider
    if selected is None and subject_id:
        selected = (
            await db.execute(
                select(AccountSettings.preferred_provider)
                .join(UserAccount, UserAccount.id == AccountSettings.account_id)
                .where(UserAccount.profile_uuid == subject_id)
            )
        ).scalar_one_or_none()
    try:
        return get_provider(selected)
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc


def _validate_module(request: ChatRequest) -> str | None:
    if request.module and request.module not in MODULE_PROMPTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown module {request.module!r}; expected one of "
            f"{sorted(MODULE_PROMPTS)}",
        )
    return request.module


async def _resume_or_create(
    request: ChatRequest,
    store: SessionStore,
    db: AsyncSession,
    *,
    subject_id: str | None,
):
    """Get the live session, rehydrating a persisted one when it has been lost.

    The session store is in-process and TTL'd, so it does not survive a backend
    restart, a 6-hour gap, or a second worker. `get_or_create` treats an id it
    does not recognise as a guess and mints a fresh one — safe, but it means
    the very next message after any of those events lands in a *new*
    conversation while the client still displays the old one. That is what
    produces two sidebar rows for what the person experienced as one chat, and
    it is why the same account open on two devices appeared not to sync.

    So before giving up on a client-supplied id, check whether the database has
    a conversation with that id belonging to this authenticated subject. If it
    does, the id is not a guess — it is theirs — and the session is rebuilt
    from the stored transcript under its original id. `get_or_create`'s
    refusal to adopt unknown ids is preserved for every other case: an
    unauthenticated caller, or an id nobody owns, still starts fresh.
    """
    if request.session_id:
        live = await store.get(request.session_id)
        from ..v2_profile import enabled as v2_enabled
        if live is not None and not (v2_enabled() and subject_id):
            return live

        if subject_id:
            result = await db.execute(
                select(Conversation).where(
                    Conversation.session_id == request.session_id,
                    Conversation.subject_id == subject_id,
                )
            )
            conversation = result.scalar_one_or_none()
            if conversation is not None:
                runtime_state = await db.get(
                    ConversationRuntimeState, conversation.id
                )
                logger.info(
                    "resuming persisted session %s (%d messages)",
                    request.session_id,
                    len(conversation.messages),
                )
                return await store.adopt(
                    request.session_id,
                    [
                        Message(
                            role=m.role,
                            content=normalize_reasoning_channels(
                                m.content, m.reasoning_content
                            ).reply,
                        )
                        for m in conversation.messages
                    ],
                    runtime_state.module if runtime_state else None,
                    runtime_state.memory if runtime_state else None,
                )

    return await store.get_or_create(request.session_id)


async def _initial_state(
    request: ChatRequest,
    store: SessionStore,
    db: AsyncSession,
    *,
    subject_id: str | None,
) -> tuple[AgentState, str]:
    """Seed the graph's state and make sure the session exists.

    Request metadata is merged into the session here, before the graph runs, so
    `extract_memory_node` reads one merged view rather than having to reconcile
    request and session metadata itself.
    """
    session = await _resume_or_create(request, store, db, subject_id=subject_id)
    if request.metadata:
        session.metadata.update(request.metadata)

    state: AgentState = {
        "session_id": session.session_id,
        "user_input": request.message,
        "metadata": dict(session.metadata),
        "forced_module": _validate_module(request),
        "subject_id": subject_id,
        "turn_started_monotonic": perf_counter(),
    }
    return state, session.session_id


def _context(
    provider: LLMProvider,
    store: SessionStore,
    *,
    stream: bool,
    router_prompt: str,
) -> GraphContext:
    settings = get_settings()
    router_provider_name = settings.router_provider_name
    return GraphContext(
        provider=provider,
        # Module transitions use the configured routing provider, independently
        # from the provider that generates the visible coaching reply.  This
        # keeps routing available when one provider is out of service (the
        # production DeepSeek account can be unpaid while Ark remains active).
        router_provider=get_provider(router_provider_name),
        store=store,
        knowledge_base=get_knowledge_base(),
        settings=settings,
        memos=get_memos_manager(settings),
        # The factory, not a session: the clinical writes this feeds happen in
        # detached background tasks that outlive this request, so they must
        # open their own connection rather than borrow one FastAPI is about to
        # return to the pool.
        sessionmaker=get_sessionmaker(),
        router_prompt=router_prompt,
        stream=stream,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    store: SessionStore = Depends(get_session_store),
    db: AsyncSession = Depends(get_db),
    subject_id: str | None = Depends(optional_subject_id),
) -> ChatResponse:
    request_started = perf_counter()
    settings = get_settings()
    await wait_for_pending_routing(
        request.session_id,
        timeout_seconds=settings.routing_wait_timeout_seconds,
        # Do not cancel the previous decision merely because the user sent a
        # follow-up. It may still contain the evidence needed to finish a
        # module transition; schedule_background_routing supersedes it safely
        # after the new assistant row is durable.
        cancel_on_timeout=False,
    )
    provider = await _resolve_provider(request, db, subject_id=subject_id)
    router_prompt = await effective_router_prompt(db)
    state, session_id = await _initial_state(request, store, db, subject_id=subject_id)
    user_message_id = await _persist_user_message(
        db,
        subject_id=subject_id,
        session_id=session_id,
        user_text=request.message,
    )
    state["user_message_id"] = user_message_id
    state["turn_started_monotonic"] = request_started
    context = _context(provider, store, stream=False, router_prompt=router_prompt)

    turn_lock = await store.get_turn_lock(session_id)
    async with turn_lock:
        final_state = await get_graph().ainvoke(
            state,
            context=context,
        )

        reply = final_state.get("final_response", "")
        reasoning_content = final_state.get("reasoning_content", "")
        model_name = final_state.get("model", provider.model)
        routing_reasoning = final_state.get("routing_reasoning_content", "")
        router_model_name = final_state.get("router_model_name", "")
        assistant_message_id = await _persist_assistant_message(
            db,
            subject_id=subject_id,
            session_id=session_id,
            user_message_id=user_message_id,
            reply_text=reply,
            reasoning_content=reasoning_content,
            model_name=model_name,
            provider_name=final_state.get("provider", provider.name),
            routing_reasoning_content=routing_reasoning,
            router_model_name=router_model_name,
            telemetry=final_state.get("telemetry"),
        )
        live = await store.get(session_id)
        if subject_id and live is not None:
            await save_runtime_state(
                db,
                session_id=session_id,
                module=live.module,
                memory=live.memory,
            )
        schedule_background_routing(
            final_state,
            context,
            assistant_message_id=assistant_message_id,
        )

    # The module node converts provider failures into `error` on the state so
    # the graph still converges; surface that as a 502 here.
    if final_state.get("error"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=final_state["error"]
        )

    return ChatResponse(
        session_id=session_id,
        reply=reply,
        reasoning_content=reasoning_content,
        routing_reasoning_content=routing_reasoning,
        router_model_name=router_model_name,
        provider=final_state.get("provider", provider.name),
        model=final_state.get("model", provider.model),
        reply_module=final_state["extracted_intent"],
        next_module=final_state.get("next_module"),
        routing_pending=False,
        routed_by=final_state["routed_by"],
        usage=final_state.get("usage", {}),
    )


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    store: SessionStore = Depends(get_session_store),
    db: AsyncSession = Depends(get_db),
    subject_id: str | None = Depends(optional_subject_id),
) -> StreamingResponse:
    try:
        control = generations.begin(subject_id, str(request.generation_id or uuid4()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        response = await control.run(_prepare_chat_stream(request, store, db, subject_id, control))
    except GenerationStopped:
        generations.finish(control)
        async def stopped():
            yield _sse("cancelled", {"cancelled": True})
        return StreamingResponse(stopped(), media_type="text/event-stream")
    except BaseException:
        generations.finish(control)
        raise

    async def tracked_body():
        try:
            async for frame in response.body_iterator:
                yield frame
        finally:
            try:
                await response.body_iterator.aclose()
            finally:
                generations.finish(control)
    return StreamingResponse(tracked_body(), media_type="text/event-stream", headers=dict(response.headers))


@router.post("/chat/cancel")
async def cancel_generation(
    request: CancelGenerationRequest,
    subject_id: str = Depends(require_subject_id),
) -> dict:
    # Owner + random per-turn id, not a client-provided user/session identity.
    # A delayed stop cannot cancel the next turn or another user's request.
    try:
        return {"status": await generations.cancel(subject_id, str(request.generation_id))}
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


async def _prepare_chat_stream(
    request: ChatRequest, store: SessionStore, db: AsyncSession,
    subject_id: str | None, control: GenerationControl,
) -> StreamingResponse:
    request_started = perf_counter()
    settings = get_settings()
    await wait_for_pending_routing(
        request.session_id,
        timeout_seconds=settings.routing_wait_timeout_seconds,
        cancel_on_timeout=False,
    )
    provider = await _resolve_provider(request, db, subject_id=subject_id)
    router_prompt = await effective_router_prompt(db)
    state, session_id = await _initial_state(request, store, db, subject_id=subject_id)
    user_message_id = await _persist_user_message(
        db,
        subject_id=subject_id,
        session_id=session_id,
        user_text=request.message,
    )
    state["user_message_id"] = user_message_id
    state["turn_started_monotonic"] = request_started
    context = _context(
        provider, store, stream=True, router_prompt=router_prompt
    )
    context.generation = control
    # The request-scoped dependency survives as long as StreamingResponse, but
    # the model may think for minutes. End the setup transaction now so that
    # idle time does not pin a MySQL connection; final writes use a fresh,
    # short-lived session below and remain safe on client cancellation.
    await db.rollback()

    async def event_source():
        nonlocal state, session_id
        # Emitted before the graph starts so a brand-new client can store its
        # session id even if the run fails immediately.
        yield _sse(
            "meta",
            {
                "session_id": session_id,
                "provider": provider.name,
                "model": provider.model,
                "user_message_id": user_message_id,
            },
        )

        reply_parts: list[str] = []
        reasoning_parts: list[str] = []
        streamed_routing_reasoning = ""
        streamed_router_model = ""
        final_state: AgentState | None = None
        saw_done = False
        saw_error = False
        first_visible_at = None
        turn_lock = await store.get_turn_lock(session_id)
        async with turn_lock:
            try:
                # Ask for the running state as well as custom token events.
                # LangGraph 1.2.11 can complete a graph while yielding no
                # custom events in some production runtimes. The final values
                # stream is therefore the durable fallback: it guarantees a
                # completed model reply is still sent and persisted instead
                # of silently disappearing after the initial meta event.
                async for item in control.iterate(get_graph().astream(
                    state, context=context, stream_mode=["custom", "values"]
                )):
                    if (
                        isinstance(item, tuple)
                        and len(item) == 2
                        and item[0] in {"custom", "values"}
                    ):
                        mode, event = item
                    else:
                        # Compatibility with graph/test doubles that emit the
                        # original custom-only shape.
                        mode, event = "custom", item

                    if mode == "values":
                        if isinstance(event, dict):
                            final_state = event
                        continue
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("type")
                    if kind == "trace":
                        logger.debug("graph trace: %s", event)
                        continue
                    if kind == "delta":
                        if event.get("text") and first_visible_at is None:
                            first_visible_at = perf_counter()
                        reply_parts.append(event.get("text", ""))
                    elif kind == "reasoning_delta":
                        reasoning_parts.append(event.get("text", ""))
                    elif kind == "routing_reasoning":
                        streamed_routing_reasoning = str(event.get("text", ""))
                        streamed_router_model = str(event.get("model", ""))
                    elif kind == "done":
                        saw_done = True
                    elif kind == "error":
                        saw_error = True
                    if kind in CLIENT_EVENTS:
                        payload = {k: v for k, v in event.items() if k != "type"}
                        yield _sse(kind, payload)
            except GenerationStopped:
                # The user row is already durable. Do not save unvalidated
                # partial output, advance memory, or start the background router.
                await store.append(session_id, Message(role="user", content=request.message))
                yield _sse("cancelled", {"cancelled": True, "session_id": session_id})
                return
            except Exception as exc:  # noqa: BLE001
                # Headers are already sent, so a raised exception would just
                # sever the connection. Report it in-band instead.
                logger.exception("graph run failed for session %s", session_id)
                yield _sse("error", {"detail": str(exc)})
                saw_error = True

            streamed_reply = "".join(reply_parts)
            streamed_reasoning = "".join(reasoning_parts)
            final_reply = (
                str(final_state.get("final_response") or "")
                if final_state is not None
                else streamed_reply
            )
            final_error = (
                str(final_state.get("error") or "")
                if final_state is not None
                else ""
            )
            final_reasoning = (
                str(final_state.get("reasoning_content") or "")
                if final_state is not None
                else streamed_reasoning
            )
            final_model = (
                str(final_state.get("model") or provider.model)
                if final_state is not None
                else provider.model
            )
            final_routing_reasoning = (
                str(final_state.get("routing_reasoning_content") or "")
                if final_state is not None
                else streamed_routing_reasoning
            )
            final_router_model = (
                str(final_state.get("router_model_name") or "")
                if final_state is not None
                else streamed_router_model
            )

            # If custom deltas vanished, send the answer once at completion.
            # If only the tail vanished, send just that missing suffix.
            if final_reply and final_reply != streamed_reply:
                if not streamed_reply:
                    first_visible_at = first_visible_at or perf_counter()
                    yield _sse("delta", {"text": final_reply})
                elif final_reply.startswith(streamed_reply):
                    yield _sse("delta", {"text": final_reply[len(streamed_reply) :]})

            # Apply the same final-state fallback to reasoning. This matters
            # on runtimes where LangGraph completes but drops custom events.
            if final_reasoning and final_reasoning != streamed_reasoning:
                if not streamed_reasoning:
                    yield _sse("reasoning_delta", {"text": final_reasoning})
                elif final_reasoning.startswith(streamed_reasoning):
                    yield _sse(
                        "reasoning_delta",
                        {"text": final_reasoning[len(streamed_reasoning) :]},
                    )

            if final_routing_reasoning and not streamed_routing_reasoning:
                yield _sse(
                    "routing_reasoning",
                    {"text": final_routing_reasoning, "model": final_router_model},
                )

            if final_error and not saw_error:
                yield _sse("error", {"detail": final_error})
                saw_error = True
            elif not final_error and not saw_done:
                yield _sse(
                    "done",
                    {
                        "session_id": session_id,
                        "reply_module": (
                            final_state.get("extracted_intent")
                            if final_state is not None
                            else None
                        ),
                        "next_module": (
                            None
                            if final_state is None
                            else final_state.get("next_module")
                        ),
                        "routing_pending": False,
                        "routed_by": (
                            final_state.get("routed_by")
                            if final_state is not None
                            else None
                        ),
                        "usage": (
                            final_state.get("usage", {})
                            if final_state is not None
                            else {}
                        ),
                    },
                )

            # The user row was committed before generation. Appending the
            # reply before releasing the turn lock means the next device reads
            # a complete first turn from the live session and the database.
            if final_state is not None and first_visible_at is not None:
                metrics = dict(final_state.get("telemetry") or {})
                metrics["time_to_first_visible_content_ms"] = round((first_visible_at - state["turn_started_monotonic"]) * 1000, 3)
                metrics["first_visible_measurement"] = "server_sse_release"
                metrics["execution_timeline"] = [*(metrics.get("execution_timeline") or []), {
                    "stage": "response_display", "start_ms": metrics["time_to_first_visible_content_ms"],
                    "duration_ms": 0, "status": "released", "after_display": False}]
                final_state["telemetry"] = metrics
            async with get_sessionmaker()() as final_db:
                assistant_message_id = await _persist_assistant_message(
                    final_db,
                    subject_id=subject_id,
                    session_id=session_id,
                    user_message_id=user_message_id,
                    reply_text=final_reply or streamed_reply,
                    reasoning_content=final_reasoning or streamed_reasoning,
                    model_name=final_model,
                    provider_name=(
                        final_state.get("provider", provider.name)
                        if final_state is not None else provider.name
                    ),
                    routing_reasoning_content=(
                        final_routing_reasoning or streamed_routing_reasoning
                    ),
                    router_model_name=final_router_model or streamed_router_model,
                    telemetry=(
                        final_state.get("telemetry") if final_state is not None else None
                    ),
                )
                live = await store.get(session_id)
                if subject_id and live is not None:
                    await save_runtime_state(
                        final_db,
                        session_id=session_id,
                        module=live.module,
                        memory=live.memory,
                    )

            if final_state is not None:
                schedule_background_routing(
                    final_state,
                    context,
                    assistant_message_id=assistant_message_id,
                )

            # Unlike graph "done", this acknowledges the durable transcript.
            # Clients may stop reading here even if a mobile proxy holds EOF.
            yield _sse("persisted", {"saved": assistant_message_id is not None})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # don't let nginx buffer the stream
        },
    )


@router.get("/modules")
async def list_modules() -> dict[str, list[str]]:
    """Module names the frontend can pin via `module` on a chat request."""
    return {"modules": list(MODULE_PROMPTS)}


@router.get("/graph")
async def describe_graph() -> dict:
    """The compiled DAG's shape — handy for debugging and for docs."""
    graph = get_graph().get_graph()
    return {
        "nodes": [node.id for node in graph.nodes.values()],
        "edges": [
            {"source": edge.source, "target": edge.target, "conditional": edge.conditional}
            for edge in graph.edges
        ],
    }


@router.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(
    session_id: str, store: SessionStore = Depends(get_session_store)
) -> SessionInfo:
    session = await store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return SessionInfo(
        session_id=session.session_id,
        message_count=len(session.messages),
        next_module=session.module,
        memory=dict(session.memory),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str, store: SessionStore = Depends(get_session_store)
) -> None:
    await store.reset(session_id)
