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
        status = (await db.execute(select(plans.c.record_status).join(cycles, cycles.c.module_two_record_id == plans.c.id)
            .where(cycles.c.id == runtime['active_cycle_id'], cycles.c.goal_id == runtime['active_goal_id'],
                   plans.c.goal_id == runtime['active_goal_id']))).scalar_one_or_none()
        goal_selected = runtime.get('active_goal_id') is not None
        from .program_confirmation import draft
        pending = await draft(db, runtime, user_id)
        summary_fields = ("activity_content", "schedule_text", "location", "duration_minutes", "frequency_rule",
                          "potential_barriers", "barrier_coping_plan", "negotiated_record_plan")
        return {"available": True, "current_module": runtime['current_module'],
                "flow_status": runtime['flow_status'], "row_version": runtime['row_version'],
                "goal_selected": goal_selected, "plan_confirmed": status == 'confirmed',
                "draft_for_dialogue_summary": {k: pending[k] for k in summary_fields if k in pending} if pending else None}


def workflow_prompt(authority):
    import json
    return ('# 对话内确认及系统状态（优先于旧提示词和历史错误回复）\n'
        + json.dumps(authority, ensure_ascii=False)
        + '\n目标讨论、计划确认及复盘决定都在对话中完成，由后台核验证据后保存和推进。目标面板只读回顾，不决定目标归属。'
          '禁止要求去目标面板、网页面板或按钮核对、确认、保存或提交。此前引导有误应直接道歉纠正，不说用户误会、不编造界面用途。'
          'M1愿意开始目标设定，仅代表愿意进入讨论，不代表已有具体目标。未选定活动时不得假定已有目标。'
          '只有系统状态明确已发生时才可陈述已保存、已锁定、已切换；不要提前宣称完成。'
          'M2且goal_selected=false时先解释身体活动、探索意向，在对话中由用户明确选择活动，不要求手动创建目标。'
          'M2已有目标时，在聊天中简明完整复述当前计划的活动、时间、地点、分钟数、频率、困难和应对，询问是否愿意尝试。'
          '复述应保留字段的具体措辞，不改写数值或约定；用户修改后须复述新版本再确认，不拿旧同意确认新计划。'
          'M3在聊天中完整复述商定的记录方式，并询问用户是否同意。'
          '用户明确同意后后台核验；未通过时继续在聊天里核对缺少的内容，不转交面板，不重复索取已经生效的同意。'
          '状态不可用时不宣称任何保存或流转已经发生。')


def truthful_workflow_reply(authority):
    names = {'module_1':'问题理解','module_2':'目标设定','module_3':'记录约定','module_4':'执行与复盘'}
    if not authority.get('available'):
        return '讨论和确认都可以在这里完成。目前我暂时无法核实保存状态，因此不会把尚未核实的计划说成已经保存。'
    if authority['current_module'] == 'module_1':
        return '抱歉，让你去目标面板确认的引导不对。愿意开始讨论目标，不代表已经订好了目标；我们在这里一步步讨论就可以，不需要去其他页面确认。你现在想开始聊聊想尝试的活动，还是先把前面的问题说清楚？'
    if authority['current_module'] == 'module_2':
        if not authority.get('goal_selected', False):
            return '我们还没有选定具体目标，也不需要去目标面板确认。可以先在这里聊聊你想尝试的活动，等你觉得合适，再一起把安排说清楚。'
        return '计划的讨论和确认都在这里完成，不需要去目标面板操作。我们先把想尝试的活动和安排说清楚，有需要调整的地方也可以直接告诉我。'
    current = names.get(authority['current_module'], '当前')
    return f'我们仍在{current}阶段，讨论和确认都在这里完成，不需要去目标面板操作。你可以直接告诉我需要调整的地方。'
