from types import SimpleNamespace
import pytest
from app.supporters import effective_supporters
from app.workflow_state import required_steps_complete, apply_router_decision
from app.workflow_contract import MODULE_STEP_KEYS, step_prompt_contract
from test_workflow_state import _seed, SESSION, SUBJECT


def profile():
    return SimpleNamespace(supporter1_relation="朋友",supporter1_nickname="旧人",supporter1_influence="强",
                           supporter2_relation=None,supporter2_nickname=None,supporter2_influence=None)


def test_supporter_absent_and_cleared_are_distinct():
    assert len(effective_supporters(profile(),None))==1
    assert len(effective_supporters(profile(),SimpleNamespace(supporters=None)))==1
    assert effective_supporters(profile(),SimpleNamespace(supporters=[]))==[]


def test_full_list_is_authoritative():
    data=[{"relation":"室友","nickname":str(i)} for i in range(4)]
    assert len(effective_supporters(profile(),SimpleNamespace(supporters=data)))==4


def test_registry_and_unknown_module():
    assert required_steps_complete("not-a-module",[]) is False
    for module,keys in MODULE_STEP_KEYS.items():
        assert required_steps_complete(module,list(keys))
        assert all(key in step_prompt_contract() for key in keys)


async def test_no_advance_without_steps(db_sessionmaker):
    await _seed(db_sessionmaker)
    async with db_sessionmaker() as db:
        with pytest.raises(ValueError):
            await apply_router_decision(db,session_id=SESSION,subject_id=SUBJECT,current_module="module_1",
                target_module="module_2",completed_steps=[])
        await db.rollback()


def test_supporter_api_rejects_conflict_and_preserves_third(client,auth_headers):
    people=[{"relation":"室友","nickname":"一"},{"relation":"朋友","nickname":"二"},{"relation":"教练","nickname":"三"}]
    res=client.patch('/api/profile',headers=auth_headers,json={"supporters":people})
    assert res.status_code==200
    res=client.patch('/api/profile',headers=auth_headers,json={"supporter1_nickname":"改名"})
    assert res.status_code==200 and len(res.json()['supporters'])==3
    assert res.json()['supporters'][0]['relation']=='室友'
    assert res.json()['supporters'][2]['nickname']=='三'
    assert client.patch('/api/profile',headers=auth_headers,json={"supporters":[],"has_supporter":True}).status_code==422
    assert client.patch('/api/profile',headers=auth_headers,json={"supporters":[],"supporter1_nickname":"冲突"}).status_code==422
    assert len(client.get('/api/profile',headers=auth_headers).json()['supporters'])==3
