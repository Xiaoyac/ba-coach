"""Read-only, bounded V2 facts for the knowledge mediator. No free SQL tools."""
from sqlalchemy import select
from .database_v2_schema import metadata
from .v2_workflow import runtime_for
from .v2_repository import active_memories


async def assemble_knowledge_context(maker, user_id, session_id):
    facts = []
    async with maker() as db:
        conversation, state = await runtime_for(db, session_id)
        if not conversation or conversation.subject_id != user_id or not state:
            return {"goal_id": None, "cycle_id": None, "facts": []}
        def fact(table, row, fields):
            facts.append({"source": table, "record_id": str(row.get("id") or row.get("user_id")),
                "version": row.get("version_no", row.get("row_version")),
                "confirmation": row.get("confirmation_status"),
                "status": row.get("status", row.get("record_status")),
                "source_kind": row.get("source_kind", row.get("source_type")),
                "source_message_id": row.get("source_message_id", row.get("confirmation_message_id")),
                "validity": {"valid_from": str(row.get("valid_from")) if row.get("valid_from") else None,
                             "valid_until": str(row.get("valid_until")) if row.get("valid_until") else None},
                "values": {k: row[k] for k in fields if k in row}})
        for name, key, identifier, fields in [
            ("pa_goals", "id", state["active_goal_id"], ["title", "status"]),
            ("pa_cycles", "id", state["active_cycle_id"], ["goal_id", "status", "module_two_record_id"]),
        ]:
            t = metadata.tables[name]
            row = (await db.execute(select(t).where(t.c[key] == identifier))).mappings().one_or_none()
            if row:
                fact(name, row, fields)
        plans = metadata.tables["module_two_record"]
        cycle = metadata.tables["pa_cycles"]
        plan_id = (await db.execute(select(cycle.c.module_two_record_id).where(
            cycle.c.id == state["active_cycle_id"], cycle.c.goal_id == state["active_goal_id"]))).scalar_one_or_none()
        scope = plans.c.id == plan_id if plan_id else ((plans.c.goal_id == state["active_goal_id"]) & (plans.c.record_status == "draft"))
        plan = (await db.execute(select(plans).where(scope, plans.c.goal_id == state["active_goal_id"])
            .order_by(plans.c.version_no.desc()).limit(1))).mappings().one_or_none()
        if plan:
            fact(plans.name, plan, ["activity_content", "schedule_text", "location", "duration_minutes", "frequency_rule", "potential_barriers", "barrier_coping_plan"])
        m1state, m1 = metadata.tables["user_module_one_state"], metadata.tables["module_one_record"]
        one = (await db.execute(select(m1).join(m1state, m1state.c.confirmed_formulation_id == m1.c.id)
            .where(m1state.c.user_id == user_id, m1.c.user_id == user_id))).mappings().one_or_none()
        if one:
            fact(m1.name, one, ["chief_complaint", "functional_chain_summary"])
        t = metadata.tables["user_activity_constraints"]
        rows = (await db.execute(select(t).where(t.c.user_id == user_id, t.c.status == "active").order_by(t.c.updated_at.desc()).limit(21))).mappings().all()
        if len(rows) > 20:
            raise ValueError("Safety context exceeds the bounded contract; withhold retrieval")
        for row in rows:
            fact(t.name, row, ["constraint_type", "content", "constraint_text", "severity"])
        for row in (await active_memories(db, user_id=user_id))[:10]:
            fact("ba_memory", row, ["memory_type", "content"])
        return {"goal_id": state["active_goal_id"], "cycle_id": state["active_cycle_id"], "facts": facts}
