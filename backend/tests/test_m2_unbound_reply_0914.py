import pytest

from app.reply_workflow import truthful_workflow_reply, workflow_prompt


def test_unbound_m2_reply_keeps_chat_open_without_fake_draft():
    authority = {"available": True, "current_module": "module_2", "goal_selected": False}
    reply = truthful_workflow_reply(authority)
    # DB state can lag behind a choice expressed in the current user turn.
    # Do not falsely deny that choice; simply avoid claiming a confirmed plan.
    assert "具体安排还需要在对话里核对" in reply
    assert "不需要去目标面板确认" in reply
    assert "尝试的活动" in reply


def test_unbound_m2_reply_explains_blocked_goal_creation_without_blame():
    reply = truthful_workflow_reply({
        "available": True, "current_module": "module_2", "goal_selected": False,
        "goal_creation": {
            "status": "blocked", "reason_code": "proposal_evidence_missing",
            "message": "没有找到用户明确选择该活动的可核对证据",
        },
    })
    assert "目标还没有创建" in reply
    assert "没有从当前对话提取到可核对的活动选择记录" in reply
    assert "不是你没有表达" in reply
    assert "具体安排还需要在对话里核对" not in reply


def test_bound_m2_continues_in_dialogue():
    reply = truthful_workflow_reply({"available": True, "current_module": "module_2", "goal_selected": True})
    assert "目标面板" in reply
    assert "不需要去目标面板操作" in reply


def test_workflow_prompt_distinguishes_unbound_m2():
    prompt = workflow_prompt({"available": True, "current_module": "module_2", "goal_selected": False})
    assert "goal_selected=false" in prompt
    assert "不要求手动创建目标" in prompt


@pytest.mark.parametrize("module", ["module_1", "module_3", "module_4"])
def test_other_modules_remain_truthful(module):
    reply = truthful_workflow_reply({"available": True, "current_module": module, "goal_selected": False})
    assert "不需要去" in reply
    assert "已保存" not in reply
