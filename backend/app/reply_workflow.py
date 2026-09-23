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
        # Goal creation runs in the detached router.  Keep its durable
        # diagnostic beside the live authority so the next visible reply can
        # explain a blocked creation instead of falling back to the same
        # generic M2 sentence.  This is deliberately read-only: the router
        # remains the only writer of the workflow decision.
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
            # M1 has no confirmation_readiness row, but it still has a
            # durable evidence contract. Read it on every reply so the model
            # sees the current missing topic (especially an education gap),
            # rather than relying on one-time clinical context.
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
            if pending and result['extraction_fresh'] and runtime['current_module'] in {'module_2', 'module_3'}:
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
            evidence = contract.get('evidence') or {}
            if 'm4_milestone_1' in missing:
                m4_status['next_action'] = '先补本轮真实执行/未开始反馈及可核验结果，不讨论收尾。'
            elif 'm4_milestone_2' in missing:
                m4_status['next_action'] = '先补完整ABC事实并让用户核对，不用收尾回顾替代ABC。'
            elif 'm4_milestone_3' in missing:
                if not evidence.get('education_quote'):
                    m4_status['next_action'] = '基于本轮已核对的经历，实际解释行动、情绪与反馈之间的关系，再了解用户是否理解；不能只说已理解或直接收尾。'
                else:
                    m4_status['next_action'] = '已有真实BA说明，先回应尚未解决的核心疑问或了解用户对这段说明的理解，不重讲新一遍使原有理解过期，也不重问已有决定。'
            elif 'm4_milestone_4' in missing:
                if pending.get('review_decision') in {2, 3, 4}:
                    m4_status['next_action'] = '核对用户在理解之后选择的调整、更换、暂停或结束方向；这些方向不强求继续原计划的应对策略。'
                else:
                    m4_status['next_action'] = '核对真实困难及继续原计划时共同选择的应对策略；没有实际困难不强造困难，不能拿未被用户选择的旧策略补齐。'
            elif 'm4_milestone_5' in missing:
                m4_status['next_action'] = '基于已核对事实和用户决定补完整收尾总结；不得只重复收到或宣布已结束。'
            elif 'm4_evidence_refresh' in missing:
                m4_status['next_action'] = '本轮证据尚未刷新，先接住用户内容并等待核验，不声称已保存。'
        summary_fields = ("activity_content", "schedule_text", "location", "duration_minutes", "frequency_rule",
                          "potential_barriers", "barrier_coping_plan", "negotiated_record_plan")
        return {"available": True, "current_module": runtime['current_module'],
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
    import json
    prompt = ('# 对话内确认及系统状态（优先于旧提示词和历史错误回复）\n'
        + json.dumps(authority, ensure_ascii=False)
        + '\n目标讨论、计划确认及复盘决定都在对话中完成，由后台核验证据后保存和推进。目标面板只读回顾，不决定目标归属。'
          '禁止要求去目标面板、网页面板或按钮核对、确认、保存或提交。此前引导有误应直接道歉纠正，不说用户误会、不编造界面用途。'
          'M1愿意开始目标设定，仅代表愿意进入讨论，不代表已有具体目标。未选定活动时不得假定已有目标。'
          '只有系统状态明确已发生时才可陈述已保存、已锁定、已切换；不要提前宣称完成。'
          'M2且goal_selected=false时先解释身体活动、探索意向，在对话中由用户明确选择活动，不要求手动创建目标。用户表示不知道如何安排或请求建议时，基于已知偏好主动给出2-3个可调整方案，再请用户选择或修改，不把自主选择误写成教练不能提供建议。'
          'M2已有目标时，在聊天中简明完整复述当前计划的活动、时间、地点、分钟数、频率、困难和应对，询问是否愿意尝试。'
          '复述应保留字段的具体措辞，不改写数值或约定；用户修改后须复述新版本再确认，不拿旧同意确认新计划。'
          'M3在聊天中完整复述商定的记录方式，并询问用户是否同意。'
          '用户明确同意后后台核验；未通过时继续在聊天里核对缺少的内容，不转交面板，不重复索取已经生效的同意。'
          'readiness.reasons 是统一的未完成原因：缺少字段时只补该项；缺少展示证据时完整复述当前草稿请用户核对。'
          'record_missing 或 extraction_not_fresh 是系统尚未整理成功，不代表用户没有回答或没有同意；请接住已有信息，不重复要求同意。'
          'M4 的 readiness.missing_fields 指明复盘实际还缺什么。若只缺 m4_milestone_5，应基于本轮真实执行、已核对的ABC、已理解的原理和用户决定，给出完整简短的收尾总结；不要只说收到或反复问是否结束。'
          'M4用户重复同一决定时，接住该决定并补齐尚缺的收尾总结，不重复索取同意。用户改变事实或方向时按新的内容重新核对。'
          'M4在 flow_status 尚非 completed/paused 时，说“你选择结束/暂停这个目标”或“这次我们总结如下”，不要声称目标已结束、复盘已保存或下一周期已开启。'
          'goal_creation.status=blocked 时，直接说明后台阻断原因；proposal_evidence_missing 表示系统没有提取到可核对记录，不等于用户没有表达，不重复要求用户用同一句话确认。'
          '状态不可用时不宣称任何保存或流转已经发生。')
    m1 = authority.get('m1_status') if isinstance(authority, dict) else None
    if isinstance(m1, dict) and authority.get('current_module') == 'module_1':
        missing = set(m1.get('missing_fields') or [])
        topics = m1.get('education_missing_topics') or []
        prompt += ('\nM1最新证据状态（每轮都以此为准，不以旧历史推断）：'
                   + json.dumps(m1, ensure_ascii=False) + '。')
        if 'ba_understanding' in missing and topics:
            prompt += ('当前真正缺的是 BA 教育内容：本轮必须先用自然语言逐项补充 education_missing_topics 列出的主题，'
                       '每个主题用自己的明确连续句解释，不能只说“我收到/你已理解”，不能用同一句泛泛教育代替多个主题，'
                       '也不能跳去问具体活动或重复索取目标设定同意。补充后再回应用户的疑问；若仍需核对理解，只问一个与这段补充直接相关的问题。')
        elif 'ba_understanding' in missing:
            prompt += ('当前缺 BA 基本理解证据：先回应未解决的疑问或用一句具体解释帮助用户理解，'
                       '不要重复索取已经表达的目标意愿。')
        elif 'goal_setting_consent' in missing:
            prompt += ('当前只缺目标设定意愿：结合用户已有上下文自然澄清一次，不重讲 BA 教育，也不要假定已有具体活动目标。')
    m4 = authority.get('m4_status') if isinstance(authority, dict) else None
    if isinstance(m4, dict) and authority.get('current_module') == 'module_4':
        prompt += ('\nM4最新证据状态（每轮都以此为准）：' + json.dumps(m4, ensure_ascii=False) + '。'
                   '按 next_action 只补当前首个缺项；用户已说结束/暂停也不能跳过前置证据。')
    return prompt


def truthful_workflow_reply(authority):
    names = {'module_1':'问题理解','module_2':'目标设定','module_3':'记录约定','module_4':'执行与复盘'}
    if not authority.get('available'):
        return '讨论和确认都可以在这里完成。目前我暂时无法核实保存状态，因此不会把尚未核实的计划说成已经保存。'
    if authority['current_module'] == 'module_1':
        return '讨论和确认都在这里完成，不需要去其他页面操作。我们先把你提到的经历理解清楚：当时发生了什么，你有什么感受？'
    if authority['current_module'] == 'module_2':
        if not authority.get('goal_selected', False):
            creation = authority.get('goal_creation') if isinstance(authority.get('goal_creation'), dict) else None
            if creation and creation.get('status') == 'blocked':
                code = creation.get('reason_code')
                if code == 'proposal_evidence_missing':
                    return ('我收到你要按这份安排执行，但目标还没有创建。'
                            '后台这次没有从当前对话提取到可核对的活动选择记录；这不是你没有表达，'
                            '我也不会把它说成已经保存。请让我重新核对这份安排后再提交。')
                if code in {'activity_missing', 'plan_fields_missing'}:
                    return ('我收到你的目标意愿，但目标还没有创建。计划里还有必需信息没有整理完整，'
                            '我会先把缺少的内容核对清楚，再保存目标，不把未完成的草稿说成已建立。')
                return ('我收到你的目标意愿，但目标还没有创建。后台保存被拦截，原因是：'
                        + str(creation.get('message') or '计划证据尚未通过核验')
                        + '。我会先修正这一步，再继续当前对话。')
            return '具体安排还需要在对话里核对，不需要去目标面板确认。如果你已经有想尝试的活动，我们可以接着聊怎样安排才适合你；还没想好也没关系。'
        return '计划的讨论和确认都在这里完成，不需要去目标面板操作。我们先把想尝试的活动和安排说清楚，有需要调整的地方也可以直接告诉我。'
    current = names.get(authority['current_module'], '当前')
    return f'我们仍在{current}阶段，讨论和确认都在这里完成，不需要去目标面板操作。你可以直接告诉我需要调整的地方。'
