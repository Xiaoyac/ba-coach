"""Tests for /api/auth — registration, login, tokens, and what they gate.

These run against the throwaway SQLite database from conftest, which is also
where `BizBase` (the 7 externally-owned tables) is created — registration
genuinely inserts a `user_profile` row, so the mapping is exercised for real
rather than mocked.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select


def _register(client: TestClient, **overrides) -> dict:
    payload = {
        "username": "nanan",
        "password": "correct-horse-battery",
        "nickname": "南瓜",
        "tag": "12345",
    }
    payload.update(overrides)
    payload.setdefault("email", f"{payload['username'].lower()}@example.com")
    return client.post("/api/auth/register", json=payload)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_returns_a_usable_token(client: TestClient) -> None:
    response = _register(client)
    assert response.status_code == 201
    body = response.json()

    assert body["token"]
    assert body["account"]["username"] == "nanan"
    assert body["account"]["tag"] == "12345"
    assert body["account"]["nickname"] == "南瓜"
    assert body["account"]["display_id"] == "南瓜#12345"
    assert len(body["account"]["profile_uuid"]) == 36

    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert me.status_code == 200
    assert me.json()["username"] == body["account"]["username"]


def test_register_creates_the_clinical_profile(client: TestClient) -> None:
    """The account is useless without the `user_profile` row it points at."""
    body = _register(
        client,
        communication_preference="温柔引导",
        physical_condition=["膝关节损伤", "易疲劳"],
        behavior_taboo=["不能剧烈运动"],
    ).json()

    me = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['token']}"}
    ).json()
    # Everyone starts before module 1.
    assert me["current_module"] == "开场"
    assert me["profile_uuid"] == body["account"]["profile_uuid"]


def test_profile_preferences_are_persisted(client: TestClient, db_sessionmaker) -> None:
    """SET columns must round-trip as a list, not a comma string."""
    import asyncio

    from app.models_business import UserProfile

    body = _register(
        client,
        physical_condition=["膝关节损伤", "易疲劳"],
        behavior_taboo=["不能剧烈运动", "怕人多"],
        communication_preference="理性分析",
    ).json()
    uuid = body["account"]["profile_uuid"]

    async def read() -> UserProfile:
        async with db_sessionmaker() as db:
            return (
                await db.execute(select(UserProfile).where(UserProfile.uuid == uuid))
            ).scalar_one()

    profile = asyncio.get_event_loop().run_until_complete(read())
    assert profile.nickname == "南瓜"
    assert profile.communication_preference == "理性分析"
    assert "膝关节损伤" in profile.physical_condition
    assert "怕人多" in profile.behavior_taboo


def test_empty_preference_lists_become_null_not_empty_string(
    client: TestClient, db_sessionmaker
) -> None:
    """MySQL reads "" back as a one-element set containing "" — avoid it."""
    import asyncio

    from app.models_business import UserProfile

    body = _register(client).json()
    uuid = body["account"]["profile_uuid"]

    async def read() -> UserProfile:
        async with db_sessionmaker() as db:
            return (
                await db.execute(select(UserProfile).where(UserProfile.uuid == uuid))
            ).scalar_one()

    profile = asyncio.get_event_loop().run_until_complete(read())
    assert profile.physical_condition is None
    assert profile.behavior_taboo is None


def test_duplicate_login_account_is_rejected(client: TestClient) -> None:
    assert _register(client).status_code == 201
    duplicate = _register(client, nickname="另一个昵称", tag="54321")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "登录账号已被使用"


def test_login_account_is_case_insensitively_unique(client: TestClient) -> None:
    assert _register(client, username="nanan").status_code == 201
    assert _register(client, username="NaNaN", tag="54321").status_code == 409


def test_nickname_can_repeat_with_different_user_chosen_tags(client: TestClient) -> None:
    first = _register(client, username="firstuser", tag="12345").json()["account"]
    second = _register(client, username="seconduser", tag="54321").json()["account"]
    assert first["nickname"] == second["nickname"] == "南瓜"
    assert first["display_id"] == "南瓜#12345"
    assert second["display_id"] == "南瓜#54321"


def test_nickname_and_tag_combination_must_be_unique(client: TestClient) -> None:
    assert _register(client, username="firstuser").status_code == 201
    duplicate = _register(client, username="seconduser")
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "这个昵称与标签组合已被使用"


@pytest.mark.parametrize("username", ["user-name", "用户123", "name_123", "abc#12345"])
def test_login_account_only_accepts_ascii_letters_and_digits(
    client: TestClient, username: str
) -> None:
    assert _register(client, username=username).status_code == 422


@pytest.mark.parametrize("tag", ["1234", "123456", "12a45", "１２３４５"])
def test_tag_must_be_exactly_five_ascii_digits(client: TestClient, tag: str) -> None:
    assert _register(client, tag=tag).status_code == 422


def test_short_password_is_rejected(client: TestClient) -> None:
    assert _register(client, password="short").status_code == 422


def test_short_username_is_rejected(client: TestClient) -> None:
    assert _register(client, username="ab").status_code == 422


def test_unknown_preference_value_is_rejected(client: TestClient) -> None:
    """A value outside the ENUM is a 422 at the edge, not a MySQL error."""
    assert _register(client, communication_preference="随便聊聊").status_code == 422


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_with_correct_password(client: TestClient) -> None:
    account = _register(client).json()["account"]
    response = client.post(
        "/api/auth/login",
        json={
            "username": account["username"],
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 200
    assert response.json()["token"]


def test_login_never_accepts_nickname_or_display_id(client: TestClient) -> None:
    account = _register(client).json()["account"]
    response = client.post(
        "/api/auth/login",
        json={"username": account["display_id"], "password": "correct-horse-battery"},
    )
    assert response.status_code == 401


def test_login_is_case_insensitive_on_username(client: TestClient) -> None:
    _register(client, username="nanan")
    response = client.post(
        "/api/auth/login",
        json={"username": "  NANAN  ", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200


def test_wrong_password_is_rejected(client: TestClient) -> None:
    _register(client)
    response = client.post(
        "/api/auth/login", json={"username": "nanan", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_unknown_user_and_wrong_password_are_indistinguishable(
    client: TestClient,
) -> None:
    """Otherwise the login form is an account-existence oracle."""
    _register(client, username="nanan")

    wrong_password = client.post(
        "/api/auth/login", json={"username": "nanan", "password": "nope-not-it"}
    )
    unknown_user = client.post(
        "/api/auth/login", json={"username": "ghost", "password": "nope-not-it"}
    )

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_password_is_never_stored_in_the_clear(
    client: TestClient, db_sessionmaker
) -> None:
    import asyncio

    from app.models import UserAccount

    _register(client)

    async def read() -> UserAccount:
        async with db_sessionmaker() as db:
            return (await db.execute(select(UserAccount))).scalar_one()

    account = asyncio.get_event_loop().run_until_complete(read())
    assert "correct-horse-battery" not in account.password_hash
    # Argon2id, in PHC string format.
    assert account.password_hash.startswith("$argon2id$")


def test_token_is_never_stored_in_the_clear(
    client: TestClient, db_sessionmaker
) -> None:
    """A leaked dump must not contain replayable live sessions."""
    import asyncio

    from app.models import AuthSession

    token = _register(client).json()["token"]

    async def read() -> AuthSession:
        async with db_sessionmaker() as db:
            return (await db.execute(select(AuthSession))).scalar_one()

    session = asyncio.get_event_loop().run_until_complete(read())
    assert session.token_hash != token
    assert len(session.token_hash) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_logout_revokes_only_that_token(client: TestClient) -> None:
    first = _register(client).json()["token"]
    second = client.post(
        "/api/auth/login",
        json={"username": "nanan", "password": "correct-horse-battery"},
    ).json()["token"]

    assert (
        client.post(
            "/api/auth/logout", headers={"Authorization": f"Bearer {first}"}
        ).status_code
        == 204
    )

    # The logged-out token is dead...
    assert (
        client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {first}"}
        ).status_code
        == 401
    )
    # ...and the other device is still signed in.
    assert (
        client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {second}"}
        ).status_code
        == 200
    )


def test_logout_everywhere_revokes_all_sessions(client: TestClient) -> None:
    first = _register(client).json()["token"]
    second = client.post(
        "/api/auth/login",
        json={"username": "nanan", "password": "correct-horse-battery"},
    ).json()["token"]

    assert (
        client.delete(
            "/api/auth/sessions", headers={"Authorization": f"Bearer {first}"}
        ).status_code
        == 204
    )
    for token in (first, second):
        assert (
            client.get(
                "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )


def test_malformed_authorization_headers_are_rejected(client: TestClient) -> None:
    token = _register(client).json()["token"]
    for header in ("", "Bearer", "Bearer   ", token, f"Basic {token}", "Bearer wrong"):
        response = client.get("/api/auth/me", headers={"Authorization": header})
        assert response.status_code == 401, header


def test_expired_sessions_stop_working(client: TestClient, db_sessionmaker) -> None:
    import asyncio
    from datetime import timedelta

    from app.models import AuthSession, _utcnow

    token = _register(client).json()["token"]

    async def expire() -> None:
        async with db_sessionmaker() as db:
            session = (await db.execute(select(AuthSession))).scalar_one()
            session.expires_at = _utcnow() - timedelta(seconds=1)
            await db.commit()

    asyncio.get_event_loop().run_until_complete(expire())

    assert (
        client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )


# ---------------------------------------------------------------------------
# What auth gates
# ---------------------------------------------------------------------------


def test_protected_endpoints_require_a_token(client: TestClient) -> None:
    for method, path in (
        ("get", "/api/conversations"),
        ("get", "/api/assessment/status"),
        ("get", "/api/auth/me"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 401, path


def test_two_accounts_get_different_subject_ids(client: TestClient) -> None:
    a = _register(client, username="one", tag="11111").json()["account"]["profile_uuid"]
    b = _register(client, username="two", tag="22222").json()["account"]["profile_uuid"]
    assert a != b


def test_regular_accounts_report_the_user_role(client: TestClient) -> None:
    body = _register(client).json()
    assert body["account"]["role"] == "user"
    assert client.get("/api/auth/me", headers=_auth(body["token"])).json()["role"] == "user"


# ---------------------------------------------------------------------------
# Changing your own password
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_change_password_then_login_with_the_new_one(client: TestClient) -> None:
    token = _register(client).json()["token"]

    assert (
        client.post(
            "/api/auth/password",
            json={
                "current_password": "correct-horse-battery",
                "new_password": "a-brand-new-password",
            },
            headers=_auth(token),
        ).status_code
        == 204
    )

    # Old password no longer works, new one does.
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "nanan", "password": "correct-horse-battery"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "nanan", "password": "a-brand-new-password"},
        ).status_code
        == 200
    )


def test_change_password_requires_the_current_one(client: TestClient) -> None:
    """Holding a token is not enough — see ChangePasswordRequest on why."""
    token = _register(client).json()["token"]
    response = client.post(
        "/api/auth/password",
        json={"current_password": "not-my-password", "new_password": "something-else-1"},
        headers=_auth(token),
    )
    assert response.status_code == 403

    # And the password really is unchanged.
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "nanan", "password": "correct-horse-battery"},
        ).status_code
        == 200
    )


def test_no_master_password_is_accepted(client: TestClient) -> None:
    """There is no override string that satisfies `current_password`.

    Locks in the decision: this endpoint is reachable by anyone who can reach
    the API, so a universal value here would be a key to every account.
    """
    token = _register(client).json()["token"]
    for candidate in ("lyjthebest", "admin", "master", "root", ""):
        response = client.post(
            "/api/auth/password",
            json={"current_password": candidate, "new_password": "brand-new-pass-99"},
            headers=_auth(token),
        )
        assert response.status_code in (403, 422), candidate


def test_change_password_rejects_an_unchanged_password(client: TestClient) -> None:
    token = _register(client).json()["token"]
    response = client.post(
        "/api/auth/password",
        json={
            "current_password": "correct-horse-battery",
            "new_password": "correct-horse-battery",
        },
        headers=_auth(token),
    )
    assert response.status_code == 400


def test_change_password_enforces_the_length_floor(client: TestClient) -> None:
    token = _register(client).json()["token"]
    response = client.post(
        "/api/auth/password",
        json={"current_password": "correct-horse-battery", "new_password": "short"},
        headers=_auth(token),
    )
    assert response.status_code == 422


def test_change_password_revokes_other_sessions_but_not_this_one(
    client: TestClient,
) -> None:
    mine = _register(client).json()["token"]
    other_device = client.post(
        "/api/auth/login",
        json={"username": "nanan", "password": "correct-horse-battery"},
    ).json()["token"]

    client.post(
        "/api/auth/password",
        json={
            "current_password": "correct-horse-battery",
            "new_password": "a-brand-new-password",
        },
        headers=_auth(mine),
    )

    # The other device is signed out; the one that made the change is not.
    assert client.get("/api/auth/me", headers=_auth(other_device)).status_code == 401
    assert client.get("/api/auth/me", headers=_auth(mine)).status_code == 200


def test_change_password_requires_authentication(client: TestClient) -> None:
    response = client.post(
        "/api/auth/password",
        json={"current_password": "x", "new_password": "yyyyyyyyyy"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Operator (admin) reset
# ---------------------------------------------------------------------------

# 32+ chars, or the endpoint refuses to serve — see routes/auth.py.
GOOD_KEY = "k" * 44


@pytest.fixture(autouse=True)
def _clear_admin_throttle():
    """The failure counter is module state; tests must not leak into each other."""
    from app.routes import auth as auth_route

    auth_route._admin_failures.clear()
    yield
    auth_route._admin_failures.clear()


@pytest.fixture
def admin_key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("ADMIN_RESET_KEY", GOOD_KEY)
    get_settings.cache_clear()
    yield GOOD_KEY
    get_settings.cache_clear()


def test_admin_reset_is_404_when_no_key_is_configured(client: TestClient) -> None:
    """A default deploy must not advertise an admin path at all."""
    _register(client)
    response = client.post(
        "/api/auth/admin/reset",
        json={"admin_key": "anything", "username": "nanan", "new_password": "x" * 12},
    )
    assert response.status_code == 404


def test_admin_reset_refuses_a_short_key(client: TestClient, monkeypatch) -> None:
    """Refuse to run rather than pretend a guessable key is protection."""
    from app.config import get_settings

    _register(client)
    monkeypatch.setenv("ADMIN_RESET_KEY", "lyjthebest")
    get_settings.cache_clear()

    response = client.post(
        "/api/auth/admin/reset",
        json={"admin_key": "lyjthebest", "username": "nanan", "new_password": "x" * 12},
    )
    assert response.status_code == 503
    get_settings.cache_clear()


def test_admin_reset_changes_the_password(client: TestClient, admin_key: str) -> None:
    _register(client)
    response = client.post(
        "/api/auth/admin/reset",
        json={
            "admin_key": admin_key,
            "username": "nanan",
            "new_password": "operator-set-password",
        },
    )
    assert response.status_code == 204

    assert (
        client.post(
            "/api/auth/login",
            json={"username": "nanan", "password": "correct-horse-battery"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "nanan", "password": "operator-set-password"},
        ).status_code
        == 200
    )


def test_admin_reset_revokes_every_session(client: TestClient, admin_key: str) -> None:
    """An operator reset is how access gets taken back — old tokens must die."""
    token = _register(client).json()["token"]
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 200

    client.post(
        "/api/auth/admin/reset",
        json={"admin_key": admin_key, "username": "nanan", "new_password": "x" * 12},
    )
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


def test_admin_reset_rejects_a_wrong_key(client: TestClient, admin_key: str) -> None:
    _register(client)
    response = client.post(
        "/api/auth/admin/reset",
        json={"admin_key": "j" * 44, "username": "nanan", "new_password": "x" * 12},
    )
    assert response.status_code == 403
    # Password untouched.
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "nanan", "password": "correct-horse-battery"},
        ).status_code
        == 200
    )


def test_admin_reset_throttles_repeated_failures(
    client: TestClient, admin_key: str
) -> None:
    _register(client)
    body = {"admin_key": "j" * 44, "username": "nanan", "new_password": "x" * 12}

    for _ in range(5):
        assert client.post("/api/auth/admin/reset", json=body).status_code == 403

    # Sixth attempt is locked out — and stays locked even with the right key,
    # so the throttle cannot be stepped around by guessing correctly later.
    assert client.post("/api/auth/admin/reset", json=body).status_code == 429
    assert (
        client.post(
            "/api/auth/admin/reset",
            json={**body, "admin_key": admin_key},
        ).status_code
        == 429
    )


def test_admin_reset_on_unknown_username_is_404(
    client: TestClient, admin_key: str
) -> None:
    response = client.post(
        "/api/auth/admin/reset",
        json={"admin_key": admin_key, "username": "ghost", "new_password": "x" * 12},
    )
    assert response.status_code == 404


def test_admin_reset_enforces_the_password_floor(
    client: TestClient, admin_key: str
) -> None:
    _register(client)
    response = client.post(
        "/api/auth/admin/reset",
        json={"admin_key": admin_key, "username": "nanan", "new_password": "short"},
    )
    assert response.status_code == 422
