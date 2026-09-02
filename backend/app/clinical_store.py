"""Turning extracted JSON into rows in the pre-existing business tables.

Two responsibilities, deliberately separated from the extraction prompt:

1. `coerce` — decide what is allowed to reach the database at all.
2. the `persist_*` helpers — write it, in their own session.

On trust: every value here originated as model output. It is treated exactly
like a request body from the internet — unknown keys are dropped, types are
checked, strings are truncated to their column width, and integers are clamped
to the range the column and the CHECK constraints allow. The alternative is a
`Data truncated for column` or an out-of-range TINYINT taking down a
transaction inside a background task, which is both harder to notice and
harder to trace back.

On sessions: these run from detached background tasks that outlive the request
(see `graph/nodes.py`), so they must open their *own* session from the
sessionmaker. Reusing the request's session would write through a connection
FastAPI has already returned to the pool.

On rows-per-module: a subject cycles through modules 2→3→4 repeatedly
(`interaction_status.full_m2_m3_m4_cycle_count` exists precisely to count
that), so these tables accumulate one row per pass, not one row per subject.
Each pass updates the row it created until the module is left behind.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .clinical_fields import MODULE_SPECS, RISK_SPECS, Spec
from .models import ProfileExtension
from .models_business import (
    ModuleFourRecord,
    ModuleOneRecord,
    ModuleThreeRecord,
    ModuleTwoRecord,
    RiskMonitoring,
    InteractionStatus,
    UserProfile,
)
from .workflow_state import current_cycle_for_session, link_cycle_record, linked_record_id

logger = logging.getLogger(__name__)

MODULE_MODELS: dict[str, type] = {
    "module_1": ModuleOneRecord,
    "module_2": ModuleTwoRecord,
    "module_3": ModuleThreeRecord,
    "module_4": ModuleFourRecord,
}

# `user_profile.current_module` is an ENUM in Chinese; the graph speaks
# "module_N". One mapping, used both ways.
MODULE_TO_ENUM: dict[str, str] = {
    "module_1": "模块一",
    "module_2": "模块二",
    "module_3": "模块三",
    "module_4": "模块四",
}

# Levels are the live schema's 0–2 codes. `execution_result` / `review_decision` /
# `risk_expression_type` are small codes with their own documented ranges.
_INT_RANGES: dict[str, tuple[int, int]] = {
    "execution_result": (1, 4),
    "review_decision": (1, 3),
    "risk_status": (0, 1),
    "risk_expression_type": (1, 3),
    "target_activity_duration_minutes": (0, 24 * 60),
}


def _coerce_one(spec: Spec, value: Any) -> Any | None:
    """One field, or None if it cannot be made safe. Never raises."""
    if value is None or value == "":
        return None

    try:
        if spec.kind == "text":
            text = str(value).strip()
            # TEXT is 64KB in MySQL; this cap is about keeping a runaway
            # generation out of the record, not about the column limit.
            return text[:8000] or None

        if spec.kind == "varchar":
            text = str(value).strip()
            return text[: spec.max_length or 255] or None

        if spec.kind == "json":
            # Already a dict/list from json.loads; re-serialising and parsing
            # guarantees it is actually JSON-encodable before SQLAlchemy tries.
            if isinstance(value, str):
                value = json.loads(value)
            if not isinstance(value, (dict, list)):
                return None
            json.dumps(value)  # raises if it contains something unserialisable
            return value

        if spec.kind == "level":
            n = int(value)
            return n if 0 <= n <= 2 else None

        if spec.kind == "flag":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"true", "1", "yes", "是"}

        if spec.kind == "int":
            n = int(value)
            low, high = _INT_RANGES.get(spec.name, (0, 2**31 - 1))
            return n if low <= n <= high else None

        if spec.kind == "datetime":
            text = str(value).strip().replace("Z", "").replace("T", " ")
            return datetime.fromisoformat(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        # A single unparseable field must not discard the rest of the record.
        logger.debug("dropping unusable value for %s: %r", spec.name, value)
        return None

    return None


def coerce(specs: tuple[Spec, ...], raw: dict[str, Any]) -> dict[str, Any]:
    """Whitelist + type-check model output. Unknown keys never survive."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for spec in specs:
        if spec.name not in raw:
            continue
        value = _coerce_one(spec, raw[spec.name])
        if value is not None:
            out[spec.name] = value
    return out


async def _latest_row(db: AsyncSession, model: type, user_id: str):
    result = await db.execute(
        select(model).where(model.user_id == user_id).order_by(model.created_at.desc())
    )
    return result.scalars().first()


async def persist_module_record(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    module: str,
    user_id: str,
    data: dict[str, Any],
    reuse_latest: bool,
    cycle_id: str | None = None,
) -> str | None:
    """Write one module's extracted fields.

    `reuse_latest` is what distinguishes "still in this module, refine what we
    already wrote" from "back here on a new cycle, start a new row". The
    caller knows which because it knows whether the subject has left the
    module since that row was created.

    Only non-null extracted values are applied: a later pass that could not
    determine `core_values` must not erase what an earlier one found.
    """
    model = MODULE_MODELS.get(module)
    if model is None or not data:
        return None

    async with sessionmaker() as db:
        row = None
        if cycle_id and module in {"module_2", "module_3", "module_4"}:
            record_id = await linked_record_id(db, cycle_id=cycle_id, module=module)
            row = await db.get(model, record_id) if record_id else None
        elif reuse_latest:
            row = await _latest_row(db, model, user_id)
        if row is None:
            row = model(user_id=user_id)
            db.add(row)
            await db.flush()
            if cycle_id and module in {"module_2", "module_3", "module_4"}:
                await link_cycle_record(
                    db, cycle_id=cycle_id, module=module, record_id=row.id
                )
        for key, value in data.items():
            setattr(row, key, value)
        if module == "module_2" and cycle_id and data.get("has_target_card_generated"):
            interaction = (
                await db.execute(
                    select(InteractionStatus).where(InteractionStatus.user_id == user_id)
                )
            ).scalar_one_or_none()
            if interaction is None:
                interaction = InteractionStatus(user_id=user_id)
                db.add(interaction)
                await db.flush()
            raw_history = interaction.goal_history or []
            history = list(raw_history) if isinstance(raw_history, list) else []
            snapshot = {
                "cycle_id": cycle_id,
                "activity": row.target_activity_content,
                "time": row.target_activity_time.isoformat() if row.target_activity_time else None,
                "location": row.target_activity_location,
                "duration_minutes": row.target_activity_duration_minutes,
                "companion": row.target_activity_companion,
                "barriers": row.potential_barriers,
                "coping_plan": row.barrier_coping_plan,
            }
            history = [
                item for item in history
                if not isinstance(item, dict) or item.get("cycle_id") != cycle_id
            ]
            interaction.goal_history = (history + [snapshot])[-50:]
        await db.commit()
        record_id = row.id

    logger.info(
        "clinical: wrote %s for %s… (%d fields)", module, user_id[:8], len(data)
    )
    return record_id


async def persist_risk(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    data: dict[str, Any],
) -> None:
    """Append a `risk_monitoring` row — but only when something was flagged.

    A row per turn would bury the handful that matter under thousands of
    zeroes, so a clean turn writes nothing at all. `risk_status` defaults to 0
    at the column level, which is what "no row" already means.
    """
    if not data or not data.get("risk_status"):
        return

    async with sessionmaker() as db:
        db.add(
            RiskMonitoring(
                user_id=user_id,
                risk_status=data.get("risk_status", 0),
                risk_expression_type=data.get("risk_expression_type"),
                risk_expression_time=datetime.now(),
                risk_context=data.get("risk_context"),
                user_reaction_to_risk=data.get("user_reaction_to_risk"),
            )
        )
        await db.commit()

    logger.warning(
        "clinical: RISK FLAGGED for %s… status=%s type=%s",
        user_id[:8],
        data.get("risk_status"),
        data.get("risk_expression_type"),
    )


async def update_profile_module(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    module: str,
) -> None:
    """Compatibility wrapper: only update the user-level module-one ratchet.

    `module1_done_flag` is a one-way ratchet in the schema's own design: it is
    only ever set, never cleared. That mirrors `router_agent._clamp`'s
    in-process rule that a session cannot fall back into module 1 — this is
    the durable half of the same guarantee, so a restart cannot lose it.
    """
    if module not in MODULE_TO_ENUM:
        return

    async with sessionmaker() as db:
        profile = (
            await db.execute(select(UserProfile).where(UserProfile.uuid == user_id))
        ).scalar_one_or_none()
        if profile is None:
            return
        if module != "module_1":
            profile.module1_done_flag = True
        await db.commit()


def set_values(raw: object) -> list[str]:
    """Read a MySQL SET back as a list, whatever the driver hands over.

    aiomysql yields a `set`; the SQLite fallback used in tests yields the
    comma-joined string it was given. Both have to render the same way, or the
    prompt would silently differ between test and production.
    """
    if not raw:
        return []
    if isinstance(raw, (set, frozenset, list, tuple)):
        return [str(v) for v in raw if v]
    return [part for part in str(raw).split(",") if part]


async def load_profile_context(
    sessionmaker: async_sessionmaker[AsyncSession], *, user_id: str
) -> list[str]:
    """The subject's registration profile, rendered for the system prompt.

    This closes a hole where the whole registration form was write-only: the
    fields were persisted to `user_profile` and then read by nothing, so
    answering "我不能剧烈运动" changed the database and not one word of what
    the coach went on to suggest.

    Unlike `load_clinical_context`, this is loaded on **every** turn. Those
    facts are gated to the turns where they earn their token cost, but a
    physical limit is not context — it is a constraint on every activity the
    coach may propose, and a coach that respects it only on turn one is worse
    than one that never knew. It is a single indexed lookup by uuid.
    """
    lines: list[str] = []
    async with sessionmaker() as db:
        profile = (
            await db.execute(select(UserProfile).where(UserProfile.uuid == user_id))
        ).scalar_one_or_none()
        if profile is None:
            return []

        if profile.nickname:
            lines.append(f"称呼：{profile.nickname}（用这个称呼 ta，不要再问一次怎么称呼）")
        if profile.communication_preference:
            lines.append(f"沟通偏好：{profile.communication_preference}（按这个风格说话）")

        conditions = set_values(profile.physical_condition)
        if conditions:
            lines.append(
                f"身体状况：{'、'.join(conditions)}"
                "（建议任何身体活动前必须先考虑这些，禁止提出与之冲突的活动）"
            )

        taboos = set_values(profile.behavior_taboo)
        if taboos:
            lines.append(
                f"做不了的事：{'、'.join(taboos)}"
                "（这是硬性约束，绝对禁止建议任何涉及这些的活动）"
            )

        content_taboos = set_values(profile.content_taboo)
        if content_taboos:
            lines.append(f"不愿谈的话题：{'、'.join(content_taboos)}（不要主动提起）")

        if profile.age:
            lines.append(f"年龄：{profile.age}")
        if profile.living_status:
            lines.append(f"居住状况：{profile.living_status}")

        # Who this person could realistically do something *with*. Social
        # activity is a core BA lever, and suggesting "ask a friend along" to
        # someone who has named no one is how a plan stops being actionable.
        # Read from `profile_extensions` when it has an answer: the legacy
        # columns hold at most two supporters and only six relations, so the
        # extension is the fuller list. Falling back to the legacy pair keeps
        # profiles that predate the extension table working.
        ext = (
            await db.execute(
                select(ProfileExtension).where(ProfileExtension.profile_uuid == user_id)
            )
        ).scalar_one_or_none()

        entries: list[tuple[str | None, str | None, str | None]]
        if ext and ext.supporters:
            entries = [
                (e.get("relation"), e.get("nickname"), e.get("influence"))
                for e in ext.supporters
            ]
        else:
            entries = [
                (profile.supporter1_relation, profile.supporter1_nickname, profile.supporter1_influence),
                (profile.supporter2_relation, profile.supporter2_nickname, profile.supporter2_influence),
            ]

        supporters = []
        for relation, nick, influence in entries:
            if not relation and not nick:
                continue
            label = f"{nick}（{relation}）" if nick and relation else (nick or relation)
            supporters.append(f"{label}，影响力{influence}" if influence else label)
        if supporters:
            lines.append(f"身边的支持者：{'；'.join(supporters)}（需要有人一起的活动优先考虑 ta 们）")
        # `not ...` rather than `is False`: the flag comes back as a real bool
        # from MySQL's TINYINT(1) but as 0 from some drivers, and `0 is False`
        # is False in Python — which is why this line silently never fired.
        elif not profile.has_supporter and profile.living_status == "独居":
            lines.append("目前没有登记支持者且独居（不要默认 ta 有人可以一起行动）")

        activity = [
            f"环境{profile.activity_environment}" if profile.activity_environment else None,
            f"社交{profile.activity_social}" if profile.activity_social else None,
            f"强度{profile.activity_intensity}" if profile.activity_intensity else None,
        ]
        activity = [a for a in activity if a]
        if activity:
            lines.append(f"活动偏好：{'、'.join(activity)}")

    return lines


async def load_clinical_context(
    sessionmaker: async_sessionmaker[AsyncSession], *, user_id: str,
    session_id: str | None = None,
) -> list[str]:
    """What earlier modules established, rendered for the system prompt.

    Chiefly the PA card: it is created once in module 2 and never copied
    forward (see `models_business`), so modules 3 and 4 can only reference it
    by reading that row back. Without this the coach re-asks for a plan the
    subject already made, which reads as not having listened.
    """
    lines: list[str] = []
    async with sessionmaker() as db:
        cycle_id = (
            await current_cycle_for_session(db, session_id=session_id)
            if session_id else None
        )

        async def cycle_row(model: type, module: str):
            if cycle_id:
                record_id = await linked_record_id(
                    db, cycle_id=cycle_id, module=module
                )
                if record_id:
                    return await db.get(model, record_id)
                # A cycle exists: falling back to another cycle's latest row
                # would recreate the exact cross-cycle mixing this link fixes.
                return None
            return await _latest_row(db, model, user_id)

        card = await cycle_row(ModuleTwoRecord, "module_2")
        if card is not None and card.has_target_card_generated:
            parts = [
                f"活动：{card.target_activity_content}" if card.target_activity_content else None,
                f"时间：{card.target_activity_time}" if card.target_activity_time else None,
                f"地点：{card.target_activity_location}" if card.target_activity_location else None,
                f"时长：{card.target_activity_duration_minutes} 分钟"
                if card.target_activity_duration_minutes
                else None,
                f"同伴：{card.target_activity_companion}" if card.target_activity_companion else None,
            ]
            detail = "；".join(p for p in parts if p)
            if detail:
                lines.append(f"该用户已有 PA 目标卡片 —— {detail}")

        one = await _latest_row(db, ModuleOneRecord, user_id)
        if one is not None and one.ai_depression_cycle_summary:
            lines.append(f"此前达成的抑郁循环理解：{one.ai_depression_cycle_summary}")

        three = await cycle_row(ModuleThreeRecord, "module_3")
        if three is not None and three.negotiated_record_plan:
            lines.append(f"已约定的记录方式：{three.negotiated_record_plan}")

    return lines
