"""Read-only authority for claims about saved plans and module transitions."""
from sqlalchemy import select
from .database_v2_schema import metadata
from .v2_workflow import runtime_for


async def read_reply_workflow(maker, user_id, session_id):
    async with maker() as db:
        conversation, runtime = await runtime_for(db, session_id)
        if not conversation or conversation.subject_id != user_id or not runtime:
            return {"available": False}
        plans, cycles = metadata.tables['module_two_record'], metadata.tables['pa_cycles']
        # Retain compatibility with older persisted routing diagnostics.
        # New background work only extracts/persists facts and drafts; actual
        # module changes go through the pre-reply confirmation transaction.
        # These diagnostics are not injected as conversational instructions.
        goal_creation = None
        if runtime.get('current_module') == 'module_2' and runtime.get('active_goal_id') is None:
            from .models import AIExecutionEvent
            latest_router = (await db.execute(select(AIExecutionEvent.event_metadata).where(
                AIExecutionEvent.session_id == session_id,
                AIExecutionEvent.subject_id == user_id,
                AIExecutionEvent.stage == 'module_router',
            ).order_by(AIExecutionEvent.id.desc()).limit(1))).scalar_one_or_none()
            workflow_decision = (latest_router or {}).get('workflow_decision') if isinstance(latest_router, dict) else None
            diagnostics = (workflow_decision or {}).get('routing_diagnostics') if isinstance(workflow_decision, dict) else None
            candidate = (diagnostics or {}).get('goal_creation') if isinstance(diagnostics, dict) else None
            if isinstance(candidate, dict) and candidate.get('status') == 'blocked':
                goal_creation = {
                    'status': 'blocked',
                    'reason_code': candidate.get('reason_code'),
                    'message': candidate.get('message'),
                }
        status = (await db.execute(select(plans.c.record_status).join(cycles, cycles.c.module_two_record_id == plans.c.id)
            .where(cycles.c.id == runtime['active_cycle_id'], cycles.c.goal_id == runtime['active_goal_id'],
                   plans.c.goal_id == runtime['active_goal_id']))).scalar_one_or_none()
        goal_selected = runtime.get('active_goal_id') is not None
        from .program_confirmation import draft, confirmation_readiness
        pending = await draft(db, runtime, user_id)
        readiness = None
        confirmation_summary = None
        m1_status = None
        m4_status = None
        if runtime['current_module'] == 'module_1':
            # Keep evidence diagnostics available to backend validation.
            # workflow_prompt deliberately excludes these missing fields;
            # they do not become a main-model teaching or questioning list.
            from .m1_contract import contract_for, dialogue_status
            one = metadata.tables['module_one_record']
            m1_pending = (await db.execute(select(one).where(
                one.c.user_id == user_id, one.c.record_status == 'draft'
            ).order_by(one.c.created_at.desc()).limit(1))).mappings().one_or_none()
            contract = contract_for(m1_pending)
            if contract.get('session_id') == session_id:
                m1_status = dialogue_status(contract)
        if runtime['current_module'] in {'module_2', 'module_3', 'module_4'}:
            from .models import ConversationMessage
            latest = (await db.execute(select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id).order_by(
                    ConversationMessage.position.desc()).limit(1))).scalar_one_or_none()
            result = await confirmation_readiness(db, conversation=conversation,
                state=runtime, user_id=user_id, session_id=session_id, pending=pending,
                confirmation_user_message_id=latest.id if latest and latest.role == 'user' else None)
            readiness = {key: result[key] for key in ('ready', 'missing_fields', 'reasons')}
            if pending and result['extraction_fresh'] and runtime['current_module'] == 'module_2':
                from .dialogue_confirmation import render_confirmation_summary
                from .program_confirmation import record_hash
                text = render_confirmation_summary(runtime['current_module'], pending)
                if text:
                    confirmation_summary = {'text': text, 'record_id': pending['id'],
                                            'record_hash': record_hash(pending)}
        if runtime['current_module'] == 'module_4' and pending:
            from .m4_contract import contract_for, missing_fields
            contract = contract_for(pending)
            missing = missing_fields(pending, session_id=session_id,
                                     cycle_id=runtime.get('active_cycle_id'))
            m4_status = {
                'missing_fields': missing,
                'completed_steps': contract.get('completed_steps', []),
                'review_decision': pending.get('review_decision'),
                'scenario_type': pending.get('scenario_type'),
            }
        summary_fields = ("activity_content", "schedule_text", "location", "duration_minutes", "frequency_rule",
                          "potential_barriers", "barrier_coping_plan", "negotiated_record_plan")
        recording = {}
        if runtime['current_module'] == 'module_3':
            from .knowledge_context import read_recording_state
            recording = await read_recording_state(db, user_id, runtime)
        return {"available": True, "current_module": runtime['current_module'],
                **recording,
                "flow_status": runtime['flow_status'], "row_version": runtime['row_version'],
                "goal_selected": goal_selected, "plan_confirmed": status == 'confirmed',
                "goal_creation": goal_creation,
                "readiness": readiness,
                "m1_status": m1_status,
                "m4_status": m4_status,
                "last_transition_reason": runtime['last_transition_reason'],
                "confirmation_summary": confirmation_summary,
                "draft_for_dialogue_summary": {k: pending[k] for k in summary_fields if k in pending} if pending else None}


def workflow_prompt(authority):
    """Expose committed state, not readiness diagnostics or a next-question plan."""
    import json
    keys = ("available", "current_module", "flow_status", "goal_selected", "plan_confirmed",
            "recording_status", "recording_decision_scope")
    facts = {key: authority[key] for key in keys if key in authority}
    return ("# 已提交业务状态\n" + json.dumps(facts, ensure_ascii=False)
            + "\n字段缺失或尚未保存不代表用户未表达；状态不可用时不能声称已保存或已切换。")


def truthful_workflow_reply(authority):
    """A technical state correction must not create another coaching script."""
    if not authority.get('available'):
        return '暂时无法核实保存状态，刚才关于保存成功的回复未展示。已有对话不需要重新说明。'
    return '这条回复与实际保存状态不一致，暂未展示；当前确认状态没有因此改变。已有对话不需要重新说明。'
