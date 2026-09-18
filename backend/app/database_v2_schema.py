"""Explicit V2 business schema, isolated from automatic startup DDL.

This metadata is for migration rehearsal and the V2 repository layer. Importing
it does not create tables or switch any production reads. Legacy tables must be
archived and converted, never silently treated as V2 because their names match.
"""
from __future__ import annotations

from sqlalchemy import (Boolean, CheckConstraint, Column, ForeignKey, Index,
                        Integer, JSON, MetaData, SmallInteger, String, Table,
                        Text, UniqueConstraint, func)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy import Date, DateTime

metadata = MetaData()
timestamp = DateTime().with_variant(DATETIME(fsp=6), "mysql")


def col(name, kind=Text, *, required=False, default=None, primary=False, ref=None):
    args = [name, kind]
    if ref:
        args.append(ForeignKey(ref))
    return Column(*args, primary_key=primary, nullable=not (required or primary),
                  server_default=default)


def ident(name="id", *, primary=False, required=False, ref=None):
    return col(name, String(36), primary=primary, required=required, ref=ref)


def user_key(*, primary=False):
    return ident("user_id", primary=primary, required=True, ref="user_profile.uuid")


def times():
    return [col("created_at", timestamp, required=True, default=func.now()),
            col("updated_at", timestamp, required=True, default=func.now())]


def status(values, *, name="status", default=None):
    return [col(name, String(24), required=True, default=default or values[0]),
            CheckConstraint(name + " IN (" + ",".join(repr(v) for v in values) + ")")]


def table(name, *columns):
    return Table(name, metadata, *columns)


user_profile = table("user_profile", ident("uuid", primary=True),
    col("nickname", String(64)), col("birth_year", SmallInteger), col("birth_date", Date),
    col("reported_age", SmallInteger), col("age_reported_at", timestamp),
    col("gender", String(32)), col("occupation_status", String(32)),
    col("living_status", String(32)),
    col("timezone", String(64), required=True, default="Asia/Shanghai"),
    col("module1_done_flag", Boolean, required=True, default="0"), *times())

user_preferences = table("user_preferences", user_key(primary=True),
    col("communication_style", String(32)), col("response_verbosity", String(16)),
    col("activity_environment", String(16)), col("activity_social", String(16)),
    col("activity_atmosphere", String(16)), col("preferred_activity_intensity", String(16)),
    col("reminder_frequency", String(64)), col("reminder_time", String(5)),
    col("reminder_start_minute", SmallInteger), col("reminder_end_minute", SmallInteger),
    col("reminder_time_slot", String(32)),
    col("updated_at", timestamp, required=True, default=func.now()))

user_supporters = table("user_supporters", ident(primary=True), user_key(),
    col("position", Integer, required=True), col("relation", String(32)),
    col("nickname", String(64)), col("influence", String(8)), *times(),
    UniqueConstraint("user_id", "position"), CheckConstraint("position >= 0"),
    CheckConstraint("influence IS NULL OR influence IN ('弱','中','强')"))

user_activity_constraints = table("user_activity_constraints", ident(primary=True), user_key(),
    *status(["safety_limit", "user_boundary", "preference"], name="constraint_type"),
    col("category", String(32), required=True), col("content", required=True),
    *status(["user_stated", "profile_form", "ai_inferred", "imported"], name="source_type"),
    col("source_message_id", Integer),
    *status(["unconfirmed", "confirmed", "rejected"], name="confirmation_status"),
    *status(["active", "superseded", "inactive"]),
    ident("supersedes_id", ref="user_activity_constraints.id"), *times(),
    Index("ix_v2_constraints_user_status", "user_id", "status", "constraint_type"))

module_one_record = table("module_one_record", ident(primary=True), user_key(),
    col("version_no", Integer, required=True),
    *status(["draft", "confirmed", "superseded"], name="record_status"),
    col("chief_complaint"), col("distress_duration", String(128)),
    col("distress_frequency", String(128)), col("trigger_situation"),
    col("event_experience", JSON), col("coping_behavior"), col("coping_consequence"),
    col("functional_chain_summary"),
    *status(["unconfirmed", "partially_confirmed", "confirmed", "rejected"], name="confirmation_status"),
    col("confirmation_message_id", Integer),
    *status(["not_recorded", "explained"], name="ba_explanation_status"),
    col("ba_explanation_message_id", Integer),
    *status(["unknown", "willing", "hesitant", "declined"], name="goal_setting_willingness"),
    col("willingness_message_id", Integer),
    col("attempted_relief_methods", JSON), col("exception_positive_scene", JSON),
    *times(), UniqueConstraint("user_id", "version_no"),
    CheckConstraint("version_no > 0"),
    CheckConstraint("record_status != 'confirmed' OR (confirmation_status = 'confirmed' AND confirmation_message_id IS NOT NULL)"))

user_module_one_state = table("user_module_one_state", user_key(primary=True),
    col("completed_steps", JSON, required=True),
    ident("confirmed_formulation_id", ref="module_one_record.id"),
    *status(["in_progress", "completed", "revisit_needed"]),
    *status(["none", "user_confirmed", "legacy_imported"], name="completion_source"),
    *status(["missing", "available"], name="evidence_status"),
    col("row_version", Integer, required=True, default="0"),
    col("updated_at", timestamp, required=True, default=func.now()),
    CheckConstraint("status != 'completed' OR "
        "(completion_source = 'user_confirmed' AND evidence_status = 'available' AND confirmed_formulation_id IS NOT NULL) OR "
        "(completion_source = 'legacy_imported' AND evidence_status = 'missing')"),
    CheckConstraint("completion_source != 'legacy_imported' OR "
        "(confirmed_formulation_id IS NULL AND evidence_status = 'missing')"))

pa_goals = table("pa_goals", ident(primary=True), user_key(),
    col("title", String(255), required=True),
    *status(["draft", "active", "paused", "completed", "abandoned", "replaced"]),
    ident("module_one_record_id", ref="module_one_record.id"),
    ident("current_plan_record_id"), ident("replaced_by_goal_id", ref="pa_goals.id"),
    col("status_reason", String(255)), col("created_from_conversation_id", Integer),
    col("closed_at", timestamp), col("row_version", Integer, required=True, default="0"),
    *times(), Index("ix_v2_goals_user_status", "user_id", "status"))

module_two_record = table("module_two_record", ident(primary=True),
    ident("goal_id", required=True, ref="pa_goals.id"), col("version_no", Integer, required=True),
    *status(["draft", "confirmed", "superseded"], name="record_status"),
    col("pa_understanding_status", String(24)), col("pa_willingness_status", String(24)),
    col("core_values", JSON), col("core_values_impact"), col("activity_content"),
    col("schedule_text", String(255)), col("scheduled_start_at", timestamp),
    col("timezone", String(64), required=True), col("location", String(255)),
    col("duration_minutes", SmallInteger), col("frequency_rule", JSON),
    col("companion", String(255)), col("potential_barriers", JSON), col("barrier_coping_plan", JSON),
    *status(["unconfirmed", "partially_confirmed", "confirmed", "rejected"], name="confirmation_status"),
    col("confirmation_message_id", Integer), *times(), UniqueConstraint("goal_id", "version_no"),
    CheckConstraint("version_no > 0"), CheckConstraint("duration_minutes IS NULL OR duration_minutes BETWEEN 0 AND 1440"),
    CheckConstraint("record_status != 'confirmed' OR (confirmation_status = 'confirmed' AND confirmation_message_id IS NOT NULL AND activity_content IS NOT NULL AND schedule_text IS NOT NULL)"))

module_three_record = table("module_three_record", ident(primary=True),
    ident("goal_id", required=True, ref="pa_goals.id"),
    ident("module_two_record_id", required=True, ref="module_two_record.id"),
    col("version_no", Integer, required=True),
    *status(["draft", "confirmed", "superseded"], name="record_status"),
    col("record_requirement"), col("acceptance_status", String(24)), col("acceptance_feeling"),
    col("negotiated_record_plan", JSON), col("feedback_mechanism"),
    col("reminder_enabled", Boolean, required=True, default="0"),
    col("reminder_rule", JSON), col("reminder_text", String(255)),
    col("confirmation_message_id", Integer), *times(), UniqueConstraint("goal_id", "version_no"),
    CheckConstraint("version_no > 0"),
    CheckConstraint("record_status != 'confirmed' OR confirmation_message_id IS NOT NULL"))

pa_cycles = table("pa_cycles", ident(primary=True),
    ident("goal_id", required=True, ref="pa_goals.id"), col("ordinal", Integer, required=True),
    ident("module_two_record_id", ref="module_two_record.id"),
    ident("module_three_record_id", ref="module_three_record.id"),
    *status(["planning", "waiting_execution", "reviewing", "completed", "cancelled"]),
    col("started_from_conversation_id", Integer), col("planned_for_at", timestamp),
    col("started_at", timestamp), col("completed_at", timestamp), col("cancel_reason", String(255)),
    *times(), UniqueConstraint("goal_id", "ordinal"), CheckConstraint("ordinal > 0"),
    CheckConstraint("status IN ('planning','cancelled') OR module_two_record_id IS NOT NULL"))

pa_cycle_progress = table("pa_cycle_progress", ident("cycle_id", primary=True, ref="pa_cycles.id"),
    *[col(f"module_{n}_steps", JSON, required=True) for n in (2, 3, 4)],
    col("module_4_scenario", String(8)), col("row_version", Integer, required=True, default="0"),
    col("updated_at", timestamp, required=True, default=func.now()),
    CheckConstraint("module_4_scenario IS NULL OR module_4_scenario IN ('A','B','C')"))

module_four_record = table("module_four_record", ident(primary=True),
    ident("cycle_id", required=True, ref="pa_cycles.id"),
    *status(["draft", "confirmed"], name="record_status"),
    col("scenario_type", String(8)), col("execution_result", SmallInteger),
    col("phase_a", JSON), col("phase_b", JSON), col("phase_c", JSON), col("abc_chain_summary"),
    *status(["unconfirmed", "partially_confirmed", "confirmed", "rejected"], name="chain_confirmation_status"),
    col("confirmation_message_id", Integer), col("core_difficulty_type", String(128)),
    col("difficulty_description"), col("ba_reeducation_content"), col("next_coping_strategy"),
    col("review_decision", SmallInteger), col("review_summary"), col("confirmed_at", timestamp),
    *times(), UniqueConstraint("cycle_id"),
    CheckConstraint("execution_result IS NULL OR execution_result BETWEEN 1 AND 4"),
    CheckConstraint("review_decision IS NULL OR review_decision BETWEEN 1 AND 4"),
    CheckConstraint("record_status != 'confirmed' OR (execution_result IS NOT NULL AND review_decision IS NOT NULL AND confirmation_message_id IS NOT NULL)"))

ba_memory = table("ba_memory", ident(primary=True), user_key(),
    col("memory_type", String(32), required=True), col("memory_key", String(128), required=True),
    col("content", required=True),
    *status(["user_statement", "user_confirmation", "ai_inference", "imported"], name="source_kind"),
    col("source_message_id", Integer),
    *status(["unconfirmed", "confirmed", "rejected"], name="confirmation_status"),
    col("confidence_level", SmallInteger), *status(["active", "superseded", "rejected", "expired"]),
    ident("supersedes_id", ref="ba_memory.id"), col("valid_from", timestamp), col("valid_until", timestamp),
    *times(), CheckConstraint("confidence_level IS NULL OR confidence_level BETWEEN 0 AND 2"),
    CheckConstraint("supersedes_id IS NULL OR supersedes_id != id"),
    Index("ix_v2_memory_user_status", "user_id", "status", "memory_type"),
    Index("ix_v2_memory_user_key", "user_id", "memory_key", "created_at"))

conversation_runtime_states = table("conversation_runtime_states",
    col("conversation_id", Integer, primary=True),
    *status(["module_1", "module_2", "module_3", "module_4"], name="current_module"),
    *status(["active", "waiting_execution", "paused", "completed"], name="flow_status"),
    ident("active_goal_id", ref="pa_goals.id"), ident("active_cycle_id", ref="pa_cycles.id"),
    col("last_transition_reason", String(255)), col("row_version", Integer, required=True, default="0"),
    col("memory", JSON, required=True), col("updated_at", timestamp, required=True, default=func.now()))

ai_decision_logs = table("ai_decision_logs", col("id", Integer, primary=True),
    col("conversation_id", Integer), col("turn_id", String(64), required=True),
    ident("goal_id", ref="pa_goals.id"), ident("cycle_id", ref="pa_cycles.id"),
    col("module_name", String(16), required=True), col("decision_type", String(32), required=True),
    col("decision_value", JSON, required=True), col("reason_summary", String(1000)),
    col("evidence_message_ids", JSON), col("schema_version", SmallInteger, required=True, default="1"),
    col("created_at", timestamp, required=True, default=func.now()))

# Additive goal-model tables: historic goals stay unclassified until evidence
# exists. No parent-goal foreign key: secondary goals are independent.
pa_goal_details = table("pa_goal_details", ident("goal_id", primary=True, ref="pa_goals.id"),
    *status(["unclassified", "primary", "secondary"], name="goal_kind"),
    col("long_term_direction", String(1000)), col("source_conversation_id", Integer),
    col("source_message_id", Integer), col("evidence", JSON), *times(),
    CheckConstraint("goal_kind != 'primary' OR long_term_direction IS NOT NULL"))

pa_plan_details = table("pa_plan_details", ident("plan_id", primary=True, ref="module_two_record.id"),
    *status(["unspecified", "recurring", "one_off"], name="schedule_kind"),
    col("review_cadence", String(255)), col("difficulty", String(255)),
    col("resources", JSON), *times())

pa_activity_events = table("pa_activity_events", ident(primary=True), user_key(),
    ident("goal_id", ref="pa_goals.id"), ident("cycle_id", ref="pa_cycles.id"),
    col("source_conversation_id", Integer, required=True), col("source_message_id", Integer, required=True),
    col("event_index", SmallInteger, required=True),
    *status(["performed", "not_performed", "idea"], name="event_kind"),
    *status(["active", "superseded"]), col("superseded_by_message_id", Integer),
    col("activity_content", String(255), required=True), col("occurred_at_text", String(255)),
    col("effect", String(1000)), col("source_quote", String(2000), required=True), *times(),
    UniqueConstraint("source_message_id", "event_index"),
    Index("ix_activity_user_goal", "user_id", "goal_id", "created_at"))

pa_review_details = table("pa_review_details", ident("review_id", primary=True, ref="module_four_record.id"),
    *status(["end", "pause", "replace_keep", "replace_pause", "continue", "adjust"], name="action"),
    col("source_message_id", Integer, required=True), col("source_quote", String(2000), required=True), *times())

# Migration reconciliation is explicit, never an implicit latest-row guess.
v2_migration_issues = table("v2_migration_issues", col("id", Integer, primary=True),
    col("source_table", String(64), required=True), col("source_id", String(64), required=True),
    col("issue_code", String(64), required=True), col("resolution", String(255)),
    *status(["pending", "resolved"]), *times(),
    UniqueConstraint("source_table", "source_id", "issue_code"))
