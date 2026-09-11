import dataclasses
import pytest
from app.answer_validator import validate_answer, SAFE_REPLY
from app.graph import get_graph
from app.providers.base import StreamDelta


@pytest.mark.parametrize("reply,ids,status", [("",[],"blocked"),("x"*50001,[],"blocked"),("见 kb:123",[],"blocked"),("见 kb:123",["kb:123"],"passed"),("你愿意谈谈吗？",[],"passed")], ids=["empty","oversized","unknown-id","known-id","normal"])
def test_integrity(reply,ids,status):
    result=validate_answer(reply=reply,module="module_1",evidence_ids=ids)
    assert result["status"]==status and result["llm_calls"]==0
    assert result["semantic_verified"] is False


@pytest.mark.parametrize("module,reply",[("module_1","从明天开始每天跑步"),("module_2","我们已经确定了"),("module_3","改成游泳"),("module_4","你本周坚持了五天")])
def test_module_rules_warn_not_automatically_block(module,reply):
    assert validate_answer(reply=reply,module=module,evidence_ids=[])["status"]=="review"


@pytest.mark.parametrize("module", ["module_1","module_2","module_3","module_4"])
async def test_block_before_stream_delivery(context,provider,monkeypatch,module):
    async def bad_stream(**kwargs):
        yield StreamDelta(kind="content",text="不可靠的引用 kb:99999")
    monkeypatch.setattr(provider,"stream",bad_stream)
    context=dataclasses.replace(context,stream=True)
    events=[]; final=None
    async for kind,value in get_graph().astream({"user_input":"请解释这个方法","forced_module":module},context=context,stream_mode=["custom","values"]):
        if kind=="custom": events.append(value)
        else: final=value
    assert final["final_response"]==SAFE_REPLY
    assert final["telemetry"]["answer_validator"]["status"]=="blocked"
    assert final.get("error")
    assert "".join(e.get("text","") for e in events if e.get("type")=="delta")==SAFE_REPLY


async def test_nonstream_validates_too(context,provider):
    result=await get_graph().ainvoke({"user_input":"你好","forced_module":"module_1"},context=context)
    assert result["telemetry"]["answer_validator"]["status"]=="passed"
