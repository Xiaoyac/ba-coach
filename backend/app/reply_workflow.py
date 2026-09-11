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
        return {"available": True, "current_module": runtime['current_module'],
                "flow_status": runtime['flow_status'], "row_version": runtime['row_version'],
                "plan_confirmed": status == 'confirmed'}


def workflow_prompt(authority):
    import json
    return ('# 系统提交状态（只读权威，优先于旧 Prompt 的跳转用语）\n'
        + json.dumps(authority, ensure_ascii=False)
        + '\n用户在聊天里同意，不等于网页确认接口已提交。你不能写库、锁定目标卡或切换模块。'
          '只有权威状态明确已发生时才可陈述已保存/已锁定/已切换。'
          '当前仍是 M2 时，可以整理计划、承认用户口头同意，并提示在目标面板核对确认；'
          '不得说“目标卡片已锁定”“接下来进入模块三”。可以说“网页确认成功后才能进入下一步”。'
          '状态不可用时，不宣称任何保存或流转已经发生。')


def truthful_workflow_reply(authority):
    names = {'module_1':'问题理解','module_2':'目标设定','module_3':'记录约定','module_4':'执行与复盘'}
    if not authority.get('available'):
        return '暂时无法核实保存和进度状态，请刷新目标面板后查看；我不能确认计划已保存或流程已切换。'
    current = names.get(authority['current_module'], '当前阶段')
    if authority['current_module']=='module_2':
        return '我收到你的想法了。系统当前仍在目标设定阶段，尚未切换到下一模块。请在目标面板核对草稿；如有待补内容，我们继续讨论，准备好后再点击确认。'
    return f'系统当前处于{current}阶段，实际保存和进度以目标面板为准。我们按当前进度继续。'
