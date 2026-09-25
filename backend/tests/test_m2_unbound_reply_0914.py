import pytest
from app.reply_workflow import truthful_workflow_reply, workflow_prompt

@pytest.mark.parametrize("module", ["module_1", "module_2", "module_3", "module_4"])
@pytest.mark.parametrize("selected", [False, True])
def test_state_correction_does_not_invent_a_coaching_question(module, selected):
    reply = truthful_workflow_reply({"available": True, "current_module": module, "goal_selected": selected})
    assert "保存状态不一致" in reply
    assert "已有对话不需要重新说明" in reply
    assert "目标面板" not in reply and "？" not in reply
    assert "已经保存" not in reply


def test_unavailable_state_is_reported_without_guessing():
    reply = truthful_workflow_reply({"available": False})
    assert "无法核实保存状态" in reply and "请重新说明" not in reply


def test_compact_state_does_not_turn_missing_goal_into_a_task():
    prompt = workflow_prompt({"available": True, "current_module": "module_2", "goal_selected": False})
    assert '\"goal_selected\": false' in prompt
    assert "字段缺失或尚未保存不代表用户未表达" in prompt
    assert "必须追问" not in prompt
