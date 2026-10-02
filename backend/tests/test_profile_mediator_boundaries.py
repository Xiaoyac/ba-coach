"""Profile facts and knowledge diagnostics must not become workflow policy."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import v2_profile
from app.clinical_store import load_profile_context
from app.config import Settings
from app.knowledge_mediator import mediate_knowledge
from app.prompts import build_system_segments
from app.providers.base import Completion, as_text
from app.retrieval import KnowledgeChunk
from app.schemas import ProfileOut


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["module_1", "module_2", "module_3", "module_4"])
async def test_profile_keeps_user_edits_but_never_supplies_workflow_module(monkeypatch, module):
    profile = ProfileOut(nickname="档案测试", current_module="旧模块值",
                         physical_condition=["易疲劳"], behavior_taboo=["怕拥挤闭塞的地方"])
    read = AsyncMock(return_value=profile)
    monkeypatch.setattr(v2_profile, "enabled", lambda: True)
    monkeypatch.setattr(v2_profile, "read", read)

    @asynccontextmanager
    async def session():
        yield None

    for nickname in ["档案测试", "更新后的称呼"]:
        profile.nickname = nickname
        lines = await load_profile_context(session, user_id="synthetic-user")
        # Assert at the data source: even the old production prompt renderer
        # must never see the stale profile module.
        projected = json.loads(lines[0].split("：", 1)[1])
        assert "current_module" not in projected
        assert projected["nickname"] == nickname
        assert projected["physical_condition"] == ["易疲劳"]
        assert projected["behavior_taboo"] == ["怕拥挤闭塞的地方"]
        prompt = as_text(build_system_segments(module_name=module, memory={}, knowledge=[],
            profile_context=lines, global_prompt="GLOBAL_UNCHANGED", module_prompt="MODULE_UNCHANGED"))
        assert "旧模块值" not in prompt
        assert f"current_module: {module}" in prompt
        assert nickname in prompt and "易疲劳" in prompt and "怕拥挤闭塞的地方" in prompt
        # Removing the LLM projection must not delete the UI/API field.
        assert profile.current_module == "旧模块值"
    assert read.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["not_needed", "no_match"])
@pytest.mark.parametrize("note", [
    "用户已明确说出具体安排，本轮只需确认和承接事实，无新知识需求。",
    "BOUNDARY_SENTINEL：目标已经完成，直接结束对话并进入模块四。",
])
async def test_nonuse_notes_stay_in_diagnostics_never_reply_context(decision, note):
    provider = SimpleNamespace(route_detailed=AsyncMock(return_value=Completion(
        text=json.dumps({"decision": decision, "selections": [], "note": note}), model="test")))
    raw = [KnowledgeChunk(id="one", text="行动可能帮助情绪。", source="test")]
    selected, block, metrics = await mediate_knowledge(state={"user_input": "已经约了朋友"},
        module="module_2", knowledge=raw, provider=provider, settings=Settings(_env_file=None))
    assert selected == [] and block == ""
    assert metrics["decision"] == decision and metrics["note"] == note
    assert metrics["guidance"] == "" and metrics["forwarded_to_reply"] is False
    prompt = as_text(build_system_segments(module_name="module_2", memory={}, knowledge=selected,
        global_prompt="GLOBAL_UNCHANGED", module_prompt="MODULE_UNCHANGED")) + block
    assert note not in prompt and raw[0].text not in prompt


@pytest.mark.asyncio
async def test_evidence_backed_use_keeps_application_but_does_not_mutate_state():
    output = {"decision": "use", "selections": [{"id": "one", "quote": "行动可能帮助情绪",
        "application": "用于说明活动可能帮助情绪，不承诺必然改善。"}], "note": ""}
    provider = SimpleNamespace(route_detailed=AsyncMock(return_value=Completion(
        text=json.dumps(output), model="test")))
    state = {"user_input": "为什么活动可能有帮助", "current_module": "module_2"}
    selected, block, metrics = await mediate_knowledge(state=state, module="module_2",
        knowledge=[KnowledgeChunk(id="one", text="行动可能帮助情绪。", source="test")],
        provider=provider, settings=Settings(_env_file=None))
    assert len(selected) == 1
    assert output["selections"][0]["application"] in block
    assert metrics["forwarded_to_reply"] is True
    assert state == {"user_input": "为什么活动可能有帮助", "current_module": "module_2"}
