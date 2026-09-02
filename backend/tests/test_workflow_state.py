from __future__ import annotations

import pytest
from sqlalchemy import select

from app.clinical_store import load_clinical_context, persist_module_record
from app.conversation_store import finish_turn, start_turn
from app.models import (
    AIExecutionEvent,
    Conversation,
    ConversationModuleProgress,
    ConversationRuntimeState,
    PACycle,
)
from app.models_business import InteractionStatus, UserProfile
from app.workflow_state import apply_router_decision


SUBJECT = "11111111-2222-3333-4444-555555555555"
SESSION = "workflow-session"
M1_STEPS = [
    "core_problem_example",
    "depression_cycle_formulated",
    "ba_education_completed",
    "goal_setting_consent",
]


async def _seed(db_sessionmaker):
    async with db_sessionmaker() as db:
        db.add(UserProfile(uuid=SUBJECT, nickname="测试", current_module="开场"))
        conversation = Conversation(subject_id=SUBJECT, session_id=SESSION, title="测试")
        db.add(conversation)
        await db.flush()
        db.add(
            ConversationRuntimeState(
                conversation_id=conversation.id, module="module_1", memory={}
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_steps_and_cycle_are_conversation_scoped(db_sessionmaker) -> None:
    await _seed(db_sessionmaker)
    async with db_sessionmaker() as db:
        cycle_id = await apply_router_decision(
            db,
            session_id=SESSION,
            subject_id=SUBJECT,
            current_module="module_1",
            target_module="module_2",
            completed_steps=M1_STEPS,
        )
        await db.commit()

    async with db_sessionmaker() as db:
        progress = (await db.execute(select(ConversationModuleProgress))).scalar_one()
        profile = (await db.execute(select(UserProfile))).scalar_one()
        cycles = (await db.execute(select(PACycle))).scalars().all()
        assert progress.module_1_steps == M1_STEPS
        assert progress.active_cycle_id == cycle_id
        assert len(cycles) == 1 and cycles[0].status == "active"
        # ConversationRuntimeState is authoritative; legacy current_module is untouched.
        assert profile.current_module == "开场"
        assert profile.module1_done_flag is True


@pytest.mark.asyncio
async def test_clinical_card_is_read_from_the_active_cycle(db_sessionmaker) -> None:
    await _seed(db_sessionmaker)
    async with db_sessionmaker() as db:
        cycle_id = await apply_router_decision(
            db,
            session_id=SESSION,
            subject_id=SUBJECT,
            current_module="module_1",
            target_module="module_2",
            completed_steps=M1_STEPS,
        )
        await db.commit()

    await persist_module_record(
        db_sessionmaker,
        module="module_2",
        user_id=SUBJECT,
        data={
            "target_activity_content": "晚饭后散步",
            "has_target_card_generated": True,
        },
        reuse_latest=False,
        cycle_id=cycle_id,
    )
    lines = await load_clinical_context(
        db_sessionmaker, user_id=SUBJECT, session_id=SESSION
    )
    assert any("晚饭后散步" in line for line in lines)
    async with db_sessionmaker() as db:
        interaction = (await db.execute(select(InteractionStatus))).scalar_one()
        assert interaction.goal_history[0]["cycle_id"] == cycle_id
        assert interaction.goal_history[0]["activity"] == "晚饭后散步"


@pytest.mark.asyncio
async def test_finished_turn_populates_interaction_and_ai_telemetry(db_sessionmaker) -> None:
    await _seed(db_sessionmaker)
    async with db_sessionmaker() as db:
        user_id = await start_turn(
            db, subject_id=SUBJECT, session_id=SESSION, user_text="你好"
        )
        assistant_id = await finish_turn(
            db,
            session_id=SESSION,
            user_message_id=user_id,
            reply_text="你好",
            provider_name="deepseek",
            model_name="test-model",
            telemetry={
                "main_generation_duration_ms": 123,
                "usage": {"input_tokens": 10, "output_tokens": 20, "reasoning_tokens": 5},
                "prompt_version": "prompt-v1",
            },
        )

    async with db_sessionmaker() as db:
        interaction = (await db.execute(select(InteractionStatus))).scalar_one()
        event = (await db.execute(select(AIExecutionEvent))).scalar_one()
        assert interaction.total_interaction_count == 1
        assert interaction.easy_stuck_modules == {"module_1": 1}
        assert event.assistant_message_id == assistant_id
        assert event.stage == "main_generation"
        assert event.duration_ms == 123
        assert (event.input_tokens, event.output_tokens, event.reasoning_tokens) == (10, 20, 5)
        assert event.prompt_version == "prompt-v1"
