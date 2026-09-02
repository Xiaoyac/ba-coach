"""ORM binding for the 7 pre-existing MySQL business tables.

These tables already live in the database `DATABASE_URL` points at (see
`.env`) — they were designed and created by another system, not this one.
`BizBase` is therefore a *separate* `DeclarativeBase` from `app.db.Base`: it
is never passed to `Base.metadata.create_all()`, so nothing here can ever
create, alter, or drop a table. This module is a pure reverse-mapping —
every column below was read back from the live schema with `SHOW CREATE
TABLE`, not designed from scratch.

None of the seven tables declare a foreign key at the database level (an
`information_schema` check confirms it) — `user_id` / `uuid` are just plain
indexed columns the application is expected to join on itself. The mapping
below leaves them that way rather than inventing `ForeignKey`/`relationship`
wiring the schema doesn't actually have.

Two things worth flagging about the schema itself, found while mapping it:

1. `current_module` is a column on `user_profile`
   (enum: 开场/模块一/模块二/模块三/模块四), **not** on `interaction_status`.
   `interaction_status` is a pure analytics/telemetry table — engagement
   counters, decline signals, which module a subject gets stuck in — it has
   no notion of "what module is this subject in right now". Reading or
   writing "current module" means touching `UserProfile.current_module`.
   `UserProfile.module1_done_flag` is the accompanying one-way ratchet
   ("once module 1 is done it can never be re-entered" — the same rule
   `router_agent.py`'s `_clamp()` already enforces in-process).

2. The PA (target activity) card is created exactly once, in
   `module_two_record` (`target_activity_content/time/location/duration_
   minutes/companion`, `potential_barriers`, `barrier_coping_plan`,
   `has_target_card_generated`). It does not get copied forward. Later
   modules *reference* that same card rather than holding their own copy:
   `module_three_record` is about negotiating how the person will report
   back on it (not the card itself), and `module_four_record.phase_a/b/c`
   is the post-hoc ABC review of actually attempting it. So "does this
   subject have a PA card yet" is answered by
   `ModuleTwoRecord.has_target_card_generated` for their most recent row.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DECIMAL,
    JSON,
    VARCHAR,
    CHAR,
    TEXT,
    Boolean,
    DateTime,
    Integer,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.dialects.mysql import BIGINT, BINARY, ENUM, SET, TINYINT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class BizBase(DeclarativeBase):
    """Registry for the 7 externally-owned tables. Never passed to create_all."""


# --- Portable spellings of the MySQL column types ---------------------------
#
# Each of these keeps the exact MySQL type as a dialect variant while giving
# SQLAlchemy something it can also render elsewhere. That matters for exactly
# one caller — the test suite, which builds these tables in a throwaway SQLite
# database (the app itself never issues DDL for them at all). Without the
# variants, `create_all` on SQLite dies on the first `TINYINT`.
#
# Nothing about the MySQL side changes: these tables are never created from
# here, so the MySQL rendering is only ever used to *coerce values on read and
# write*, which the variant preserves exactly.


def _enum(*values: str):
    """A MySQL ENUM; a plain short string on any other dialect."""
    return String(64).with_variant(ENUM(*values), "mysql")


def _set(*values: str):
    """A MySQL SET. Elsewhere a comma-joined string — see `_set_value` in
    routes/auth.py, which is what builds that representation."""
    return String(255).with_variant(SET(*values), "mysql")


# Small signed integers used as 0–5 / 0–10 levels and as enum-ish codes.
_TinyInt = Integer().with_variant(TINYINT(), "mysql")
_UTinyInt = Integer().with_variant(TINYINT(unsigned=True), "mysql")
_BigIntPK = Integer().with_variant(BIGINT(unsigned=True), "mysql")
# TINYINT(1) is how MySQL spells a boolean; `Boolean` round-trips it as one.
_Bool = Boolean().with_variant(TINYINT(1), "mysql")


def _new_uuid() -> str:
    return str(uuid.uuid4())


class UserProfile(BizBase):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(_BigIntPK, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(CHAR(36), nullable=False, unique=True, default=_new_uuid)
    nickname: Mapped[str | None] = mapped_column(VARCHAR(64))
    age: Mapped[int | None] = mapped_column(_UTinyInt)
    living_status: Mapped[str | None] = mapped_column(_enum("独居", "和家人", "和朋友", "和恋人"))
    has_supporter: Mapped[bool] = mapped_column(_Bool, nullable=False, default=False)
    supporter1_relation: Mapped[str | None] = mapped_column(
        _enum("父母", "恋人", "子女", "朋友", "兄弟姐妹", "同事")
    )
    supporter1_nickname: Mapped[str | None] = mapped_column(VARCHAR(64))
    supporter1_influence: Mapped[str | None] = mapped_column(_enum("弱", "中", "强"))
    supporter2_relation: Mapped[str | None] = mapped_column(
        _enum("父母", "恋人", "子女", "朋友", "兄弟姐妹", "同事")
    )
    supporter2_nickname: Mapped[str | None] = mapped_column(VARCHAR(64))
    supporter2_influence: Mapped[str | None] = mapped_column(_enum("弱", "中", "强"))
    risk_level: Mapped[str | None] = mapped_column(_enum("低", "中", "高"))
    # See module docstring point 1 — this, not anything on InteractionStatus,
    # is where "what module is this subject in" actually lives.
    current_module: Mapped[str | None] = mapped_column(
        _enum("开场", "模块一", "模块二", "模块三", "模块四")
    )
    module_completion_flag: Mapped[bytes | None] = mapped_column(
        LargeBinary(4).with_variant(BINARY(4), "mysql")
    )
    module_task_completion: Mapped[dict | None] = mapped_column(JSON)
    # One-way ratchet: 0 -> 1 only, never back. Mirrors router_agent._clamp's
    # "no backslide past module 1" rule at the data layer.
    module1_done_flag: Mapped[bool] = mapped_column(_Bool, nullable=False, default=False)
    communication_preference: Mapped[str | None] = mapped_column(
        _enum("直接明了", "温柔引导", "理性分析", "轻松幽默")
    )
    reminder_frequency: Mapped[str | None] = mapped_column(
        _enum("每天一次", "隔天一次", "每三天一次", "每周一次", "仅在我主动找你时提醒", "暂时不需要提醒")
    )
    reminder_time_slot: Mapped[str | None] = mapped_column(
        _enum("早晨7-9", "上午9-12", "中午12-14", "下午14-18", "傍晚18-21", "晚上21-23")
    )
    physical_condition: Mapped[str | None] = mapped_column(
        _set(
            "膝关节损伤", "腰背酸痛", "慢性疼痛", "易疲劳", "睡眠障碍",
            "偏头痛", "哮喘", "眩晕", "鼻炎", "术后恢复期",
        )
    )
    behavior_taboo: Mapped[str | None] = mapped_column(
        _set("不能剧烈运动", "不能久站", "不能晒太阳", "怕吵闹", "怕人多", "怕拥挤闭塞的地方", "不坐公共交通")
    )
    content_taboo: Mapped[str | None] = mapped_column(
        _set(
            "不谈工作", "不谈学习", "不谈家庭", "不谈身材外貌", "不谈感情",
            "不谈未来计划", "不喜欢被比较", "反感正能量说教", "不喜欢被经常催促",
        )
    )
    expression_style: Mapped[str | None] = mapped_column(_enum("理性", "情绪化", "回避", "混合"))
    activity_environment: Mapped[str | None] = mapped_column(_enum("室内", "户外", "都可以"))
    activity_social: Mapped[str | None] = mapped_column(_enum("独自", "一对一", "群体", "都可以"))
    activity_intensity: Mapped[str | None] = mapped_column(_enum("安静", "热闹", "都可以"))

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UserProfile {self.uuid[:8]}… {self.current_module!r}>"


class InteractionStatus(BizBase):
    """Analytics/telemetry only — no `current_module` here. See module docstring."""

    __tablename__ = "interaction_status"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[str] = mapped_column(CHAR(36), nullable=False, unique=True)

    last_active_at: Mapped[datetime | None] = mapped_column(DateTime)
    consecutive_inactive_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_interaction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_weekly_interaction_frequency: Mapped[float | None] = mapped_column(DECIMAL(8, 2))
    good_response_behaviors: Mapped[list | dict | None] = mapped_column(JSON)
    easy_stuck_modules: Mapped[list | dict | None] = mapped_column(JSON)
    decline_behavior_signals: Mapped[list | dict | None] = mapped_column(JSON)
    decline_somatic_signals: Mapped[list | dict | None] = mapped_column(JSON)
    decline_cognitive_signals: Mapped[list | dict | None] = mapped_column(JSON)
    full_m2_m3_m4_cycle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    longest_stay_module: Mapped[str | None] = mapped_column(VARCHAR(64))
    goal_history: Mapped[list | dict | None] = mapped_column(JSON)
    behavior_activation_level_change: Mapped[list | dict | None] = mapped_column(JSON)
    overall_emotion_trend: Mapped[str | None] = mapped_column(VARCHAR(255))
    has_entered_closure_or_transition: Mapped[bool] = mapped_column(_Bool, nullable=False, default=False)
    self_coaching_confidence_level: Mapped[int | None] = mapped_column(_TinyInt)
    reaction_to_ai_coach_ending: Mapped[str | None] = mapped_column(TEXT)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InteractionStatus user={self.user_id[:8]}…>"


class ModuleOneRecord(BizBase):
    __tablename__ = "module_one_record"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)

    chief_complaint: Mapped[str | None] = mapped_column(TEXT)
    distress_duration: Mapped[str | None] = mapped_column(VARCHAR(64))
    distress_frequency: Mapped[str | None] = mapped_column(VARCHAR(64))
    trigger_situation: Mapped[str | None] = mapped_column(TEXT)
    abc_event: Mapped[dict | None] = mapped_column(JSON)
    coping_behavior: Mapped[str | None] = mapped_column(TEXT)
    coping_consequence: Mapped[str | None] = mapped_column(TEXT)
    ai_depression_cycle_summary: Mapped[str | None] = mapped_column(TEXT)
    user_approval_level: Mapped[int | None] = mapped_column(_TinyInt)
    attempted_relief_methods: Mapped[list | dict | None] = mapped_column(JSON)
    exception_positive_scene: Mapped[list | dict | None] = mapped_column(JSON)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ModuleOneRecord user={self.user_id[:8]}…>"


class ModuleTwoRecord(BizBase):
    """Where the PA (target activity) card is created — see module docstring."""

    __tablename__ = "module_two_record"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)

    pa_understanding_level: Mapped[int | None] = mapped_column(_TinyInt)
    pa_approval_level: Mapped[int | None] = mapped_column(_TinyInt)
    core_values: Mapped[str | None] = mapped_column(TEXT)
    core_values_impact: Mapped[str | None] = mapped_column(TEXT)
    target_activity_content: Mapped[str | None] = mapped_column(VARCHAR(255))
    target_activity_time: Mapped[datetime | None] = mapped_column(DateTime)
    target_activity_location: Mapped[str | None] = mapped_column(VARCHAR(255))
    target_activity_duration_minutes: Mapped[int | None] = mapped_column(Integer)
    target_activity_companion: Mapped[str | None] = mapped_column(VARCHAR(32))
    potential_barriers: Mapped[list | dict | None] = mapped_column(JSON)
    barrier_coping_plan: Mapped[list | dict | None] = mapped_column(JSON)
    has_target_card_generated: Mapped[bool] = mapped_column(_Bool, nullable=False, default=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ModuleTwoRecord user={self.user_id[:8]}… card={bool(self.has_target_card_generated)}>"


class ModuleThreeRecord(BizBase):
    __tablename__ = "module_three_record"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)

    ai_record_requirement: Mapped[str | None] = mapped_column(TEXT)
    user_acceptance_level: Mapped[int | None] = mapped_column(_TinyInt)
    user_acceptance_feeling: Mapped[str | None] = mapped_column(TEXT)
    negotiated_record_plan: Mapped[str | None] = mapped_column(TEXT)
    has_contract_reached: Mapped[bool] = mapped_column(_Bool, nullable=False, default=False)
    difficulty_feedback_mechanism: Mapped[str | None] = mapped_column(TEXT)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ModuleThreeRecord user={self.user_id[:8]}…>"


class ModuleFourRecord(BizBase):
    """Post-hoc ABC review of an attempt at the module-two PA card."""

    __tablename__ = "module_four_record"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)

    execution_result: Mapped[int | None] = mapped_column(_TinyInt)
    phase_a: Mapped[dict | None] = mapped_column(JSON)
    phase_b: Mapped[dict | None] = mapped_column(JSON)
    phase_c: Mapped[dict | None] = mapped_column(JSON)
    ai_abc_chain_summary: Mapped[str | None] = mapped_column(TEXT)
    user_chain_approval_level: Mapped[int | None] = mapped_column(_TinyInt)
    core_difficulty_type: Mapped[str | None] = mapped_column(VARCHAR(128))
    difficulty_description: Mapped[str | None] = mapped_column(TEXT)
    ba_reeducation_content: Mapped[str | None] = mapped_column(TEXT)
    next_coping_strategy: Mapped[str | None] = mapped_column(TEXT)
    review_decision: Mapped[int | None] = mapped_column(_TinyInt)
    review_summary: Mapped[str | None] = mapped_column(TEXT)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ModuleFourRecord user={self.user_id[:8]}…>"


class RiskMonitoring(BizBase):
    __tablename__ = "risk_monitoring"

    id: Mapped[str] = mapped_column(CHAR(36), primary_key=True, default=_new_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    user_id: Mapped[str] = mapped_column(CHAR(36), nullable=False)

    risk_status: Mapped[int] = mapped_column(_Bool, nullable=False, default=False)
    risk_expression_type: Mapped[int | None] = mapped_column(_TinyInt)
    risk_expression_time: Mapped[datetime | None] = mapped_column(DateTime)
    risk_context: Mapped[dict | None] = mapped_column(JSON)
    user_reaction_to_risk: Mapped[str | None] = mapped_column(VARCHAR(255))
    ai_intervention_record: Mapped[dict | None] = mapped_column(JSON)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RiskMonitoring user={self.user_id[:8]}… status={self.risk_status}>"
