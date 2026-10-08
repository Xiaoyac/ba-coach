"""Administrator test conversations own separate goals and evidence.

Member account archives retain their existing continuity. Role always comes
from authentication-owned tables, never a client flag or an LLM field.
"""
from sqlalchemy import select, false
from .database_v2_schema import metadata as schema
from .models import AccountSettings, UserAccount, Conversation, ConversationMessage


async def isolated(db, user_id):
    return await db.scalar(select(AccountSettings.role).join(
        UserAccount, UserAccount.id == AccountSettings.account_id).where(
        UserAccount.profile_uuid == user_id)) == 'admin'


async def goal_filter(db, user_id, conversation_id=None):
    goals = schema.tables['pa_goals']
    owned = goals.c.user_id == user_id
    if await isolated(db, user_id):
        return owned & (goals.c.created_from_conversation_id == conversation_id if conversation_id is not None else false())
    return owned


async def require_goal(db, user_id, goal_id, conversation_id):
    from .v2_repository import V2NotFound
    goals = schema.tables['pa_goals']
    found = await db.scalar(select(goals.c.id).where(
        goals.c.id == goal_id, await goal_filter(db, user_id, conversation_id)))
    if found is None:
        raise V2NotFound('目标不属于当前会话')


async def m1_record(db, user_id, conversation_id, *, confirmed=False):
    """No account-latest fallback for an administrator's test evidence."""
    if conversation_id is None:
        return None
    session_id = await db.scalar(select(Conversation.session_id).where(
        Conversation.id == conversation_id, Conversation.subject_id == user_id))
    if not session_id:
        return None
    table = schema.tables['module_one_record']
    from .m1_contract import contract_for
    query = select(table).where(table.c.user_id == user_id,
        table.c.record_status == 'confirmed' if confirmed else table.c.record_status != 'superseded')
    rows = (await db.execute(query.order_by(table.c.version_no.desc(), table.c.id.desc()))).mappings()
    for row in rows:
        if contract_for(row).get('session_id') == session_id:
            return row
    return None


async def m1_state(db, user_id, conversation_id=None):
    if not await isolated(db, user_id):
        table = schema.tables['user_module_one_state']
        return (await db.execute(select(table).where(table.c.user_id == user_id))).mappings().one_or_none()
    record = await m1_record(db, user_id, conversation_id, confirmed=True)
    if not record:
        return None
    from .m1_contract import contract_for
    return {'status':'completed', 'completion_source':'user_confirmed', 'evidence_status':'available',
        'confirmed_formulation_id':record['id'], 'completed_steps':contract_for(record).get('completed_steps',[])}


async def scoped_runtime(db, conversation, state):
    if not state or not await isolated(db, conversation.subject_id):
        return state
    state = dict(state)
    if state['active_goal_id']:
        goals = schema.tables['pa_goals']
        valid = await db.scalar(select(goals.c.id).where(goals.c.id == state['active_goal_id'],
            await goal_filter(db, conversation.subject_id, conversation.id)))
        if not valid:
            # Existing accidental bindings remain in storage for audit, but
            # cannot grant read/write authority over another test conversation.
            state.update(active_goal_id=None, active_cycle_id=None)
            state['memory'] = {k:v for k,v in (state['memory'] or {}).items() if k not in {
                'pa_card','last_reviewed_cycle','dialogue_draft','module_extraction_freshness','m2_activity_context'}}
    return state
