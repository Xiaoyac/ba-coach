"""Server preflight: synthetic checks + optional exact admin read-only replay.

Never saves messages, contracts or workflow state. Live provider check uses
only a synthetic preference, never the owner's personal history.
"""
import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.database_v2_schema import metadata
from app.m1_contract import consent_is_current, normalize, contract_for
from app.models import Conversation, ConversationMessage
from app.providers.deepseek import DeepSeekProvider
from app.reply_recovery import recover_empty_reply
from app.schemas import Message


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


async def main():
    question = "你愿意试试用这种方式，接下来一起设定一个具体、可操作的小目标吗？如果愿意，我们就往下走走看；有顾虑也可以直接说。"
    turns = [("assistant", question), ("user", "我愿意")]
    assert consent_is_current(turns, 1)
    assert not consent_is_current(turns + [("user", "先不要开始目标设定")], 1)
    assert not consent_is_current([("assistant", "你愿意了解目标设定的原理吗？"), ("user", "我愿意")], 1)
    emit(synthetic_consent="PASS", database_writes=0)
    if "--live" not in sys.argv:
        return
    cfg = get_settings()
    # Use the owner's requested account only; no other participant transcripts.
    engine = create_async_engine(cfg.database_url)
    try:
        async with engine.connect() as db:
            from sqlalchemy import text
            user_id = (await db.execute(text("SELECT profile_uuid FROM user_accounts WHERE username=:name"),
                {"name": "admin"})).scalar_one()
            conversation = (await db.execute(select(Conversation.__table__).where(
                Conversation.id == 112, Conversation.subject_id == user_id))).mappings().one_or_none()
            if not conversation:
                emit(owner_replay="unavailable", database_writes=0)
            else:
                records = metadata.tables["module_one_record"]
                row = (await db.execute(select(records).where(records.c.user_id == user_id,
                    records.c.record_status == "draft").order_by(records.c.created_at.desc()).limit(1))).mappings().one_or_none()
                c = contract_for(row)
                if c.get("session_id") != conversation["session_id"]:
                    emit(owner_replay="snapshot_changed", database_writes=0)
                else:
                    messages = (await db.execute(select(ConversationMessage.__table__).where(
                        ConversationMessage.conversation_id == 112,
                        ConversationMessage.id <= c["assistant_message_id"])
                        .order_by(ConversationMessage.position, ConversationMessage.id))).mappings().all()
                    transcript = [(r["role"], r["content"]) for r in messages if r["content"]]
                    e = c["evidence"]
                    consent_turn = e["consent"]["turn"]
                    assert consent_is_current(transcript, consent_turn)
                    # Revalidate the previously persisted extraction, not invented
                    # evidence and not a direct write to current_module.
                    raw = {"path": c["path"], "fact_quotes": {k: e.get(k) for k in
                        ("trigger", "feeling", "behavior", "consequence")},
                        "summary_quote": e.get("summary"), "approval_quote": e.get("approval"),
                        "methods_quote": e.get("methods"), "understanding_quote": e.get("understanding"),
                        "consent_quote": e.get("consent"), "refusal_quote": e.get("refusal"),
                        "education_quotes": [e.get("education_" + str(i)) for i in range(5)],
                        "core_questions_resolved": c.get("understanding_verified", False)}
                    data = dict(row)
                    data.update(abc_event=row["event_experience"],
                        ai_depression_cycle_summary=row["functional_chain_summary"],
                        user_approval_level=c.get("user_approval_level"))
                    fixed = normalize(raw, data, transcript, conversation["session_id"])
                    assert not fixed["missing_fields"], fixed["missing_fields"]
                    emit(owner_replay="PASS", before_missing=c["missing_fields"],
                        after_missing=fixed["missing_fields"], current_state_not_modified=True,
                        consent_turn=consent_turn, database_writes=0)
    finally:
        await engine.dispose()
    provider = DeepSeekProvider(cfg)
    try:
        result, diagnostic = await recover_empty_reply(provider=provider,
            system="你是行为激活教练。简短承接偏好，不诊断，不羞辱，不宣称已建目标，不做保存承诺。",
            messages=[Message(role="user", content="我喜欢拼图，觉得可以放松。")],
            elapsed_seconds=0, total_timeout_seconds=8, finish_reason="length", usage={})
        emit(synthetic_live_recovery=diagnostic["status"], duration_ms=diagnostic.get("duration_ms"),
             reply_chars=len(result.text) if result else 0, finish_reason=diagnostic.get("finish_reason"),
             database_writes=0)
        assert result is not None, "Live recovery probe failed"
    finally:
        await provider._client.close()


if __name__ == "__main__":
    asyncio.run(main())
