"""No database writes or network calls: goal patch smoke assertions."""
from app.dialogue_confirmation import fingerprint, summary_present
from app.goal_contract import proposal_evidence
from types import SimpleNamespace

record = {
    "activity_content": "走廊散步", "location": "办公室走廊", "duration_minutes": 10,
    "schedule_text": "下周一中午开始", "scheduled_start_at": "2026-09-21T12:30:00",
    "frequency_rule": {"schema_version": 1, "text": "每周一到周五"}, "potential_barriers": ["加班"],
    "barrier_coping_plan": [{"barrier": "加班", "plan": "先走三分钟"}],
}
card = "走廊散步；2026年9月21日12:30；办公室走廊；10分钟；每周一到周五；加班就先走三分钟。确认吗？"
assert summary_present("module_2", card, record)
assert not summary_present("module_2", card.replace("2026年", "2025年"), record)
assert not summary_present("module_2", card.replace("12:30", "11:30") + "编号1230", record)
assert fingerprint(record) == fingerprint({**record, "core_values_impact": "同义描述"})
assert fingerprint(record) != fingerprint({**record, "duration_minutes": 20})
assert fingerprint({"negotiated_record_plan": {"text": "每天记录"}}) != fingerprint(
    {"negotiated_record_plan": {"text": "每小时记录"}})
user = SimpleNamespace(role="user", content="我选择走廊散步", position=0, id=1, conversation_id=1)
assistant = SimpleNamespace(role="assistant", content="好", position=1, id=2, conversation_id=1)
proposal = {"goal_kind": "secondary", "selection_quote": user.content, "activity_quote": "走廊散步"}
assert proposal_evidence(proposal, [user, assistant], "走廊散步")
assert not proposal_evidence({**proposal, "selection_quote": "不存在的选择"}, [user, assistant], "走廊散步")
print("GOAL_PREFLIGHT_OK: selection evidence, plan confirmation, M3 fingerprint, date/time rejection")
