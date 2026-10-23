"""Admins pick their own model; 部署 makes that pick everyone else's."""
import pytest
from fastapi.testclient import TestClient

from test_admin_accounts import admin_headers  # noqa: F401
from app.config import Settings
from app.generation_policy import FLASH_MODEL, main_thinking_options
from app.providers.flash import FlashProvider
from app.schemas import Message


def _capture(monkeypatch, provider):
    from app.routes import chat as chat_route
    selected = []

    def capture(name=None):
        selected.append(name)
        return provider

    monkeypatch.setattr(chat_route, "get_provider", capture)
    return selected


def test_member_follows_deployed_choice_admin_keeps_own(
    client: TestClient, admin_headers, auth_headers, provider, monkeypatch
) -> None:
    from app.config import get_settings
    default = get_settings().default_provider
    assert client.get("/api/profile", headers=admin_headers).json()["global_provider"] == default
    assert client.patch("/api/profile", json={"preferred_provider": "doubao"},
                        headers=admin_headers).status_code == 200
    from app.routes import profile as profile_route
    monkeypatch.setattr(profile_route, "configured_providers", lambda: {"deepseek": True, "doubao": True})
    selected = _capture(monkeypatch, provider)
    client.post("/api/chat", json={"message": "你好"}, headers=auth_headers)
    assert selected[0] == default  # choosing alone changes only the admin

    deployed = client.post("/api/profile/model/deploy", headers=admin_headers)
    assert deployed.status_code == 200 and deployed.json()["global_provider"] == "doubao"
    selected.clear()
    client.post("/api/chat", json={"message": "你好"}, headers=auth_headers)
    assert selected[0] == "doubao"

    assert client.patch("/api/profile", json={"preferred_provider": "deepseek"},
                        headers=admin_headers).status_code == 200
    selected.clear()
    client.post("/api/chat", json={"message": "你好"}, headers=admin_headers)
    assert selected[0] == "deepseek"
    selected.clear()
    client.post("/api/chat", json={"message": "你好"}, headers=auth_headers)
    assert selected[0] == "doubao"


def test_only_admins_deploy_or_see_global(client: TestClient, admin_headers, auth_headers) -> None:
    assert client.post("/api/profile/model/deploy", headers=admin_headers).status_code == 409  # unconfigured
    assert client.post("/api/profile/model/deploy", headers=auth_headers).status_code == 403
    assert "global_provider" not in client.get("/api/profile", headers=auth_headers).json()


def test_flash_slot_never_thinks() -> None:
    settings = Settings(_env_file=None, deepseek_api_key="k", deepseek_model="kimi-k3",
                        deepseek_base_url="https://ark.cn-beijing.volces.com/api/coding/v3")
    flash = FlashProvider(settings)
    assert flash.model == FLASH_MODEL and flash.reply_effort_options() == ()
    scoped = flash.with_thinking(True).with_main_reply().with_deep_reply()
    assert not scoped.deep_reply_enabled and scoped.thinking_override is False
    assert scoped._main_thinking_options([Message(role="user", content="帮我想想")]) == {
        "thinking": {"type": "disabled"}}


@pytest.mark.parametrize("name", ["deepseek-v4.1-flash", "deepseek-v4-1-flash"])
def test_undated_flash_names_request_1017(name) -> None:
    settings = Settings(_env_file=None, reply_lead_model=name, knowledge_rerank_model=name,
                        risk_gate_model=name, knowledge_hybrid_mediator_model=name)
    assert {settings.reply_lead_model, settings.knowledge_rerank_model,
            settings.risk_gate_model, settings.knowledge_hybrid_mediator_model} == {FLASH_MODEL}
