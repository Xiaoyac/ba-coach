"""Read-only PA card archive. Every query is scoped to an owned goal."""
from sqlalchemy import select, func
from .database_v2_schema import metadata as schema


PLAN_FIELDS = ('id', 'version_no', 'record_status', 'confirmation_status', 'activity_content',
    'schedule_text', 'scheduled_start_at', 'timezone', 'location', 'duration_minutes',
    'frequency_rule', 'companion', 'core_values', 'core_values_impact', 'potential_barriers',
    'barrier_coping_plan', 'created_at', 'updated_at')


async def read_goal_history(db, *, user_id, goal_id, page=1, page_size=20):
    goals = schema.tables['pa_goals']
    goal = (await db.execute(select(goals).where(goals.c.id == goal_id,
        goals.c.user_id == user_id))).mappings().one_or_none()
    if goal is None:
        return None
    details = schema.tables['pa_goal_details']
    context = (await db.execute(select(details.c.goal_kind, details.c.long_term_direction)
        .where(details.c.goal_id == goal_id))).mappings().one_or_none()
    plans, cycles, events = (schema.tables[n] for n in
        ('module_two_record', 'pa_cycles', 'pa_activity_events'))
    pd, reviews, rd = (schema.tables[n] for n in
        ('pa_plan_details', 'module_four_record', 'pa_review_details'))
    offset = (page - 1) * page_size
    # Independent page totals make every historical row reachable, not a silent
    # most-recent-30 truncation. Draft and superseded plans stay explicitly labelled.
    totals = {}
    for key, table, condition in (
        ('plans', plans, plans.c.goal_id == goal_id),
        ('cycles', cycles, cycles.c.goal_id == goal_id),
        ('activities', events, (events.c.goal_id == goal_id) & (events.c.user_id == user_id))):
        totals[key] = (await db.execute(select(func.count()).select_from(table).where(condition))).scalar_one()
    plan_rows = (await db.execute(select(*[plans.c[k] for k in PLAN_FIELDS],
        pd.c.schedule_kind, pd.c.review_cadence, pd.c.difficulty, pd.c.resources)
        .select_from(plans.outerjoin(pd, pd.c.plan_id == plans.c.id)).where(plans.c.goal_id == goal_id)
        .order_by(plans.c.version_no.desc(), plans.c.id).offset(offset).limit(page_size))).mappings().all()
    cycle_rows = (await db.execute(select(cycles.c.id, cycles.c.ordinal, cycles.c.status,
        cycles.c.planned_for_at, cycles.c.started_at, cycles.c.completed_at, cycles.c.created_at,
        plans.c.version_no.label('plan_version'), plans.c.activity_content, plans.c.schedule_text,
        reviews.c.record_status.label('review_status'), reviews.c.execution_result,
        reviews.c.review_summary, rd.c.action.label('review_action'))
        .select_from(cycles.outerjoin(plans, (plans.c.id == cycles.c.module_two_record_id) &
            (plans.c.goal_id == goal_id)).outerjoin(reviews, reviews.c.cycle_id == cycles.c.id)
            .outerjoin(rd, rd.c.review_id == reviews.c.id))
        .where(cycles.c.goal_id == goal_id).order_by(cycles.c.ordinal.desc(), cycles.c.id)
        .offset(offset).limit(page_size))).mappings().all()
    event_rows = (await db.execute(select(events.c.id, events.c.event_kind, events.c.status,
        events.c.activity_content, events.c.occurred_at_text, events.c.effect, events.c.created_at,
        cycles.c.ordinal.label('cycle_ordinal')).select_from(events.outerjoin(cycles,
            (cycles.c.id == events.c.cycle_id) & (cycles.c.goal_id == goal_id)))
        .where(events.c.goal_id == goal_id, events.c.user_id == user_id)
        .order_by(events.c.created_at.desc(), events.c.id).offset(offset).limit(page_size))).mappings().all()
    return {'goal': {**{key: goal[key] for key in ('id', 'title', 'status', 'created_at', 'updated_at')},
                **(dict(context) if context else {'goal_kind': 'unclassified', 'long_term_direction': None})},
        'plans': [dict(row) for row in plan_rows], 'cycles': [dict(row) for row in cycle_rows],
        'activities': [dict(row) for row in event_rows], 'totals': totals, 'page': page, 'page_size': page_size}
