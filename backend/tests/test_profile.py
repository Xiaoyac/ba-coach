"""Tests for /api/profile — the editable half of `user_profile`.

Registration asks for what does not change plus the two safety constraints;
everything is editable here afterwards. These assert both halves, and that the
edits actually reach the coach's prompt rather than just the database — the
failure mode this whole feature exists to close.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def headers(auth_headers: dict[str, str]) -> dict[str, str]:
    return auth_headers


def _get(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.get("/api/profile", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_profile_changes_public_nickname_and_chosen_tag(
    client: TestClient, register
) -> None:
    account_headers = register(
        username="identityowner", nickname="旧昵称", tag="12345"
    )
    updated = client.patch(
        "/api/profile",
        json={"nickname": "新昵称", "tag": "54321"},
        headers=account_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["display_id"] == "新昵称#54321"
    me = client.get("/api/auth/me", headers=account_headers).json()
    assert me["username"] == "identityowner"
    assert me["display_id"] == "新昵称#54321"


def test_existing_untagged_account_can_choose_its_first_tag(
    client: TestClient, register, db_sessionmaker
) -> None:
    import asyncio

    from sqlalchemy import delete

    from app.models import AccountHandle

    account_headers = register(username="untaggedowner", nickname="保留昵称")

    async def remove_handle() -> None:
        async with db_sessionmaker() as db:
            await db.execute(delete(AccountHandle))
            await db.commit()

    asyncio.run(remove_handle())
    assert _get(client, account_headers)["display_id"] is None

    updated = client.patch(
        "/api/profile", json={"tag": "33333"}, headers=account_headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["display_id"] == "保留昵称#33333"


def test_profile_rejects_duplicate_nickname_and_tag(
    client: TestClient, register
) -> None:
    register(username="identityfirst", nickname="同名", tag="11111")
    second = register(username="identitysecond", nickname="同名", tag="22222")
    conflict = client.patch(
        "/api/profile", json={"tag": "11111"}, headers=second
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "这个昵称与标签组合已被使用"


def _patch(client: TestClient, headers: dict[str, str], **changes) -> dict:
    response = client.patch("/api/profile", json=changes, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_profile_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/profile").status_code == 401
    assert client.patch("/api/profile", json={"age": 30}).status_code == 401


def test_registration_fields_are_visible(client: TestClient, register) -> None:
    headers = register(
        username="regfields",
        nickname="南瓜",
        age=28,
        living_status="独居",
        communication_preference="温柔引导",
        physical_condition=["易疲劳"],
        behavior_taboo=["不能剧烈运动"],
    )
    body = _get(client, headers)
    assert body["nickname"] == "南瓜"
    assert body["age"] == 28
    assert body["living_status"] == "独居"
    assert body["communication_preference"] == "温柔引导"
    assert body["physical_condition"] == ["易疲劳"]
    assert body["behavior_taboo"] == ["不能剧烈运动"]
    assert body["current_module"] == "开场"


def test_model_preference_defaults_to_deepseek(client: TestClient, headers) -> None:
    response = client.get("/api/profile", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["preferred_provider"] == "deepseek"
    assert set(body["available_providers"]) == {"deepseek", "doubao"}


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def test_a_partial_edit_leaves_everything_else_alone(
    client: TestClient, register
) -> None:
    """A form that edits one section must not wipe the others."""
    headers = register(
        username="partial",
        nickname="南瓜",
        age=28,
        communication_preference="温柔引导",
        physical_condition=["易疲劳"],
    )
    body = _patch(client, headers, reminder_frequency="每天一次")

    assert body["reminder_frequency"] == "每天一次"
    assert body["nickname"] == "南瓜"
    assert body["age"] == 28
    assert body["communication_preference"] == "温柔引导"
    assert body["physical_condition"] == ["易疲劳"]


def test_model_preference_round_trips(client: TestClient, headers) -> None:
    body = _patch(client, headers, preferred_provider="doubao")
    assert body["preferred_provider"] == "doubao"
    assert _get(client, headers)["preferred_provider"] == "doubao"


def test_unknown_model_preference_is_rejected(
    client: TestClient, headers
) -> None:
    response = client.patch(
        "/api/profile",
        json={"preferred_provider": "not-a-model"},
        headers=headers,
    )
    assert response.status_code == 422


def test_an_explicit_null_clears_a_field(client: TestClient, register) -> None:
    """"Absent" and "set to null" are genuinely different requests."""
    headers = register(username="clearing", communication_preference="温柔引导")
    assert _get(client, headers)["communication_preference"] == "温柔引导"
    assert _patch(client, headers, communication_preference=None)[
        "communication_preference"
    ] is None


def test_a_healed_injury_can_be_removed(client: TestClient, register) -> None:
    """The reason these are not frozen at signup."""
    headers = register(username="healed", physical_condition=["膝关节损伤", "易疲劳"])
    body = _patch(client, headers, physical_condition=["易疲劳"])
    assert body["physical_condition"] == ["易疲劳"]

    body = _patch(client, headers, physical_condition=[])
    assert body["physical_condition"] == []


def test_every_editable_section_round_trips(client: TestClient, headers) -> None:
    from app.birth_dates import today
    body = _patch(
        client,
        headers,
        nickname="小林",
        birth_date=today().replace(year=today().year - 31, month=1, day=1).isoformat(),
        living_status="和家人",
        has_supporter=True,
        supporter1_relation="朋友",
        supporter1_nickname="阿May",
        supporter1_influence="强",
        reminder_frequency="每周一次",
        reminder_time_slot="晚上21-23",
        content_taboo=["不谈工作", "反感正能量说教"],
        activity_environment="户外",
        activity_social="一对一",
        activity_intensity="安静",
    )
    assert body["nickname"] == "小林"
    assert body["age"] == 31
    assert body["has_supporter"] is True
    assert body["supporter1_nickname"] == "阿May"
    assert body["reminder_time_slot"] == "晚上21-23"
    assert set(body["content_taboo"]) == {"不谈工作", "反感正能量说教"}
    assert body["activity_social"] == "一对一"


def test_an_empty_patch_is_rejected(client: TestClient, headers) -> None:
    assert client.patch("/api/profile", json={}, headers=headers).status_code == 400


def test_values_outside_the_enum_are_rejected(client: TestClient, headers) -> None:
    for bad in (
        {"living_status": "和室友"},
        {"communication_preference": "随便聊聊"},
        {"activity_intensity": "超级激烈"},
        {"content_taboo": ["不谈天气"]},
        {"age": 3},
        {"age": 200},
    ):
        assert client.patch("/api/profile", json=bad, headers=headers).status_code == 422, bad


def test_programme_state_cannot_be_set_by_the_subject(
    client: TestClient, headers
) -> None:
    """Posting your own `current_module` would skip the gating module 1 enforces."""
    client.patch(
        "/api/profile",
        json={"age": 30, "current_module": "模块四", "risk_level": "低"},
        headers=headers,
    )
    body = _get(client, headers)
    assert body["current_module"] == "开场", "current_module must not be settable"


def test_edits_are_scoped_to_the_caller(client: TestClient, register) -> None:
    mine = register(username="mineprofile", nickname="我")
    theirs = register(username="theirprofile", nickname="他")

    _patch(client, mine, nickname="改过的我")
    assert _get(client, theirs)["nickname"] == "他"


# ---------------------------------------------------------------------------
# Edits reaching the coach
# ---------------------------------------------------------------------------


def test_an_edit_changes_the_next_turn_s_prompt(
    client: TestClient, register, provider
) -> None:
    """The whole point: editing the profile must change what the coach is told.

    Persisting an edit that never reaches the prompt is the same bug this
    feature was built to fix, one layer up.
    """
    headers = register(username="promptedit", nickname="南瓜")

    client.post("/api/chat", json={"message": "第一轮"}, headers=headers)
    before = "\n".join(seg.text for seg in provider.systems[-1])
    assert "不能久站" not in before

    _patch(client, headers, behavior_taboo=["不能久站"], nickname="小林")

    client.post("/api/chat", json={"message": "第二轮"}, headers=headers)
    after = "\n".join(seg.text for seg in provider.systems[-1])
    assert "不能久站" in after, "the edited taboo never reached the model"
    assert "小林" in after, "the edited nickname never reached the model"


def test_clearing_a_constraint_removes_it_from_the_prompt(
    client: TestClient, register, provider
) -> None:
    headers = register(username="promptclear", behavior_taboo=["不能剧烈运动"])

    client.post("/api/chat", json={"message": "一"}, headers=headers)
    assert "不能剧烈运动" in "\n".join(seg.text for seg in provider.systems[-1])

    _patch(client, headers, behavior_taboo=[])

    client.post("/api/chat", json={"message": "二"}, headers=headers)
    assert "不能剧烈运动" not in "\n".join(seg.text for seg in provider.systems[-1])


def test_supporters_reach_the_prompt(client: TestClient, register, provider) -> None:
    """Social activity is a core BA lever; suggesting company nobody has is not."""
    headers = register(username="supporters")
    _patch(
        client,
        headers,
        has_supporter=True,
        supporter1_relation="朋友",
        supporter1_nickname="阿May",
        supporter1_influence="强",
    )
    client.post("/api/chat", json={"message": "你好"}, headers=headers)
    system = "\n".join(seg.text for seg in provider.systems[-1])
    assert "阿May" in system and "朋友" in system


# ---------------------------------------------------------------------------
# What `user_profile` structurally cannot hold
# ---------------------------------------------------------------------------


def test_more_than_two_supporters_with_custom_relations(
    client: TestClient, headers
) -> None:
    """The legacy columns allow two slots and six relations. This is neither."""
    body = _patch(
        client,
        headers,
        supporters=[
            {"relation": "朋友", "nickname": "阿May", "influence": "强"},
            {"relation": "室友", "nickname": "小周", "influence": "中"},
            {"relation": "教练", "nickname": "老陈", "influence": "弱"},
        ],
    )
    assert [s["relation"] for s in body["supporters"]] == ["朋友", "室友", "教练"]
    assert body["has_supporter"] is True


def test_supporters_project_onto_the_legacy_columns(
    client: TestClient, headers
) -> None:
    """`user_profile` must stay current, if coarser — never stale."""
    body = _patch(
        client,
        headers,
        supporters=[
            {"relation": "朋友", "nickname": "阿May", "influence": "强"},
            {"relation": "室友", "nickname": "小周", "influence": "中"},
        ],
    )
    # First one maps cleanly.
    assert body["supporter1_relation"] == "朋友"
    assert body["supporter1_nickname"] == "阿May"
    # "室友" is not in the ENUM, so the relation drops but the name survives —
    # a name with no relation beats an empty slot.
    assert body["supporter2_relation"] is None
    assert body["supporter2_nickname"] == "小周"


def test_supporters_can_be_removed(client: TestClient, headers) -> None:
    _patch(client, headers, supporters=[{"relation": "朋友", "nickname": "阿May"}])
    body = _patch(client, headers, supporters=[])
    assert body["supporters"] == []
    assert body["has_supporter"] is False
    assert body["supporter1_nickname"] is None


def test_a_precise_reminder_window_round_trips(client: TestClient, headers) -> None:
    """19:30–20:15 is not expressible in the six-bucket ENUM."""
    body = _patch(
        client, headers, reminder_window={"start_minute": 19 * 60 + 30, "end_minute": 20 * 60 + 15}
    )
    assert body["reminder_window"] == {"start_minute": 1170, "end_minute": 1215}
    # …but the legacy column still gets the bucket it falls in.
    assert body["reminder_time_slot"] == "傍晚18-21"


def test_a_window_outside_every_bucket_still_projects(
    client: TestClient, headers
) -> None:
    body = _patch(
        client, headers, reminder_window={"start_minute": 3 * 60, "end_minute": 4 * 60}
    )
    assert body["reminder_window"]["start_minute"] == 180
    assert body["reminder_time_slot"] in {"早晨7-9", "晚上21-23"}


def test_a_backwards_window_is_rejected(client: TestClient, headers) -> None:
    response = client.patch(
        "/api/profile",
        json={"reminder_window": {"start_minute": 1200, "end_minute": 600}},
        headers=headers,
    )
    assert response.status_code == 400


def test_window_minutes_are_bounded(client: TestClient, headers) -> None:
    for bad in ({"start_minute": -1, "end_minute": 60}, {"start_minute": 0, "end_minute": 2000}):
        assert (
            client.patch("/api/profile", json={"reminder_window": bad}, headers=headers).status_code
            == 422
        )


def test_supporters_reach_the_prompt_from_the_extension(
    client: TestClient, register, provider
) -> None:
    """Including the third supporter and the custom relation."""
    headers = register(username="extprompt")
    _patch(
        client,
        headers,
        supporters=[
            {"relation": "朋友", "nickname": "阿May", "influence": "强"},
            {"relation": "室友", "nickname": "小周"},
            {"relation": "教练", "nickname": "老陈"},
        ],
    )
    client.post("/api/chat", json={"message": "你好"}, headers=headers)
    system = "\n".join(seg.text for seg in provider.systems[-1])
    assert "阿May" in system
    assert "小周" in system and "室友" in system
    assert "老陈" in system, "the third supporter never reached the model"
