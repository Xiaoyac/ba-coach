import json
from types import SimpleNamespace
import pytest
from app.plan_contract import missing_plan_fields
from app.router_agent import decide_target_module_with_reasoning
from app.workflow_contract import MODULE_STEP_KEYS
from app.graph.nodes import MODULE_NODES
from app.providers.base import as_text
from app.retrieval import KnowledgeChunk
from dataclasses import replace

PLAN = dict(activity_content="读书", schedule_text="晚饭后", location="家里", duration_minutes=10,
    frequency_rule={"schema_version":1,"text":"每天"}, potential_barriers=["忘记"],
    barrier_coping_plan=[{"barrier":"忘记","plan":"书放桌上"}], difficulty_rating=4,
    difficulty_evidence={"rating": {"value": 4, "message_id": 9, "quote": "4分"}})

@pytest.mark.parametrize("key", ["activity_content", "schedule_text", "potential_barriers", "barrier_coping_plan", "difficulty_rating", "difficulty_evidence"])
def test_plan_required_field_rejected(key):
    assert key in missing_plan_fields({**PLAN,key:None})

@pytest.mark.parametrize("key", ["location", "duration_minutes", "frequency_rule"])
def test_plan_optional_fields_do_not_block(key):
    assert missing_plan_fields({**PLAN, key: None}) == []

def test_complete_plan_and_unmatched_barrier():
    assert missing_plan_fields(PLAN)==[]
    assert 'barrier_coping_plan' in missing_plan_fields({**PLAN,'potential_barriers':['下雨']})


def test_barrier_explanation_can_cover_a_canonical_barrier():
    # The extractor may keep the short obstacle as a label and put its
    # concrete explanation in a second barrier row. Both are covered by the
    # same user-provided coping plan and should not block a complete card.
    plan = {**PLAN,
        "potential_barriers": ["贪睡", "周日中午午睡容易睡过头，可能影响到两点见面"],
        "barrier_coping_plan": [
            {"barrier": "贪睡", "plan": "马哥提醒我"},
            {"barrier": "贪睡", "plan": "那我就定个闹钟"},
        ]}
    assert missing_plan_fields(plan) == []


def test_unrelated_uncovered_barrier_still_blocks():
    plan = {**PLAN, "potential_barriers": ["忘记", "下雨"]}
    assert "barrier_coping_plan" in missing_plan_fields(plan)

@pytest.mark.parametrize("valid", [True,False])
async def test_router_revocation_requires_current_quote(provider,valid):
    provider.route_result=json.dumps(dict(target_module='3',completed_steps=list(MODULE_STEP_KEYS['module_2']),
        revoked_steps=['pa_card_completed','invented'],revocation_evidence='先别执行' if valid else '编造证据'))
    result=await decide_target_module_with_reasoning(provider,current_module='module_2',user_input='先别执行，我改主意了',
        ai_output='我们重新讨论',has_pa_card=True,completed_steps=list(MODULE_STEP_KEYS['module_2']))
    assert result.revoked_steps==(['pa_card_completed'] if valid else [])
    if valid:
        assert result.target_module=='module_2'
        assert 'pa_card_completed' not in result.completed_steps

@pytest.mark.parametrize('module',['module_1','module_2','module_3','module_4'])
async def test_timeout_never_reaches_module_prompt(context,provider,module):
    class KB:
        async def search(self,**kwargs):
            return [KnowledgeChunk('sentinel','UNREVIEWED_SENTINEL','synthetic')]
    async def timeout(**kwargs):
        raise TimeoutError
    provider.route_detailed=timeout
    result=await MODULE_NODES[module]({'user_input':'请解释怎样规划活动'},SimpleNamespace(context=replace(context,knowledge_base=KB())))
    assert 'UNREVIEWED_SENTINEL' not in as_text(provider.systems[-1])
    assert result['telemetry']['knowledge_mediator']['reason']=='timeout'
