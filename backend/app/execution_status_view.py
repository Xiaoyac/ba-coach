"""Administrator-only projection: allowlisted fields, never prompts or reasoning."""
from time import time
from .execution_errors import failure_info, safe_failure
from sqlalchemy import select, or_
from .models import AIExecutionEvent, ConversationMessage
from .execution_status import STAGES, TERMINAL, snapshot, event_status


def event_row(event):
    status = event_status(event.stage, event.event_metadata, event.error_code)
    return {'stage': event.stage, 'status': status, 'model': event.model_name,
            'duration_ms': event.duration_ms, 'calls': 1,
            'failure': (safe_failure((event.event_metadata or {}).get('execution_failure'))
                        or failure_info(event.error_code, request_id=getattr(event, 'provider_request_id', None)))
                       if status == 'failed' else None}


async def execution_view(db, conversation, selected_id=None):
    users = (await db.scalars(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.role == 'user').order_by(ConversationMessage.position.desc()).limit(20))).all()
    chosen = (await db.scalar(select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.role == 'user', ConversationMessage.id == selected_id))) if selected_id is not None else (users[0] if users else None)
    if selected_id is not None and chosen is None:
        from fastapi import HTTPException
        raise HTTPException(404, 'Turn not found')
    module = None
    from .v2_profile import enabled
    if enabled():
        from .v2_workflow import runtime_for
        _, state = await runtime_for(db, conversation.session_id)
        module = state.get('current_module') if state else None
    result = {'session_id': conversation.session_id, 'user_message_id': chosen.id if chosen else None,
              'module': module, 'live': False, 'request_finished': True, 'stages': [],
              'turns': [{'id': m.id, 'preview': m.content[:70], 'created_at': m.created_at.isoformat()} for m in users],
              'server_time': time(), 'state': 'empty', 'client_first_body_ms': None}
    if chosen is None:
        return result
    assistant_id = await db.scalar(select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == conversation.id,
        ConversationMessage.position == chosen.position + 1,
        ConversationMessage.role == 'assistant'))
    filters = [AIExecutionEvent.event_metadata['user_message_id'].as_integer() == chosen.id]
    if assistant_id is not None:
        filters.append(AIExecutionEvent.assistant_message_id == assistant_id)
    events = (await db.scalars(select(AIExecutionEvent).where(
        AIExecutionEvent.conversation_id == conversation.id,
        AIExecutionEvent.subject_id == conversation.subject_id,
        or_(*filters)).order_by(AIExecutionEvent.id))).all()
    rows = {}
    for event in events:
        if (event.stage == 'client_response_display'
                and (event.event_metadata or {}).get('measurement') == 'client_click_to_body_paint'
                and type(event.duration_ms) is int and event.duration_ms >= 0):
            result['client_first_body_ms'] = event.duration_ms
        if event.stage in STAGES:
            row = event_row(event)
            row['calls'] += rows.get(event.stage, {}).get('calls', 0)
            rows[event.stage] = row
        if event.stage == 'main_generation':
            from .retrieval_timing import timing_rows
            for row in timing_rows((event.event_metadata or {}).get('retrieval') or {}):
                rows[row['stage']] = row
            for span in (event.event_metadata or {}).get('execution_timeline') or []:
                if span.get('stage') == 'retrieval' and 'retrieval' not in rows:
                    rows['retrieval'] = {'stage': 'retrieval', 'status': 'failed' if span.get('status') == 'error' else 'skipped' if span.get('status') in {'skipped', 'bypassed'} else 'completed',
                        'duration_ms': span.get('duration_ms'), 'calls': 1}
    live = snapshot(conversation.session_id, chosen.id)
    if live:
        rows.update(live['stages'])
        result.update(live=True, request_finished=live['request_finished'])
    for key, (label, lane) in STAGES.items():
        row = rows.get(key)
        if row is None:
            # Optional stages must not be presented as guaranteed pending work.
            continue
        safe = {k: row.get(k) for k in ('stage', 'status', 'model', 'duration_ms', 'calls', 'detail')}
        safe.update(label=label, lane=lane)
        safe['failure'] = safe_failure(row.get('failure')) or (failure_info() if row['status'] == 'failed' else None)
        if row['status'] not in TERMINAL and row.get('started_at') is not None:
            safe['elapsed_ms'] = max(0, round((time()-row['started_at'])*1000))
        result['stages'].append(safe)
    main = rows.get('main_generation', {}).get('status')
    running = any(row['status'] not in TERMINAL for row in rows.values())
    if main in {'failed', 'cancelled'}:
        result['state'] = 'failed' if main == 'failed' else 'cancelled'
    elif live and not live['request_finished']:
        result['state'] = 'running'
    elif running:
        result['state'] = 'background'
    elif main == 'completed':
        result['state'] = 'completed_with_errors' if any(row['status'] == 'failed' for row in rows.values()) else 'completed'
    else:
        result['state'] = 'incomplete'
    return result


async def execution_history(db, conversation, before_id=None, limit=20):
    """Older turns only; paginate by message position within the owned chat."""
    scope = (ConversationMessage.conversation_id == conversation.id,
             ConversationMessage.role == 'user')
    latest = await db.scalar(select(ConversationMessage).where(*scope)
                             .order_by(ConversationMessage.position.desc()).limit(1))
    result = {'session_id': conversation.session_id,
              'current_user_message_id': latest.id if latest else None,
              'turns': [], 'next_before_id': None}
    if latest is None:
        return result
    before_position = latest.position
    if before_id is not None:
        cursor = await db.scalar(select(ConversationMessage).where(*scope, ConversationMessage.id == before_id))
        if cursor is None:
            from fastapi import HTTPException
            raise HTTPException(404, 'Turn not found')
        before_position = min(before_position, cursor.position)
    rows = (await db.scalars(select(ConversationMessage).where(
        *scope, ConversationMessage.position < before_position)
        .order_by(ConversationMessage.position.desc()).limit(limit + 1))).all()
    page = rows[:limit]
    result['turns'] = [{'id': m.id, 'preview': m.content[:70], 'created_at': m.created_at.isoformat()} for m in page]
    result['next_before_id'] = page[-1].id if len(rows) > limit else None
    return result
