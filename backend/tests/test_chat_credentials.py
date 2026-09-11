import pytest


@pytest.mark.parametrize("endpoint", ["/api/chat", "/api/chat/stream"])
def test_invalid_login_cannot_silently_generate_anonymous_reply(client, endpoint):
    result = client.post(endpoint, json={"message": "测试失效登录"}, headers={"Authorization": "Bearer invalid-session"})
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
