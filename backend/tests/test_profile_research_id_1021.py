"""被试编号 in 我的档案: three digits, unique, and the same value 账号管理 shows."""
from test_admin_accounts import admin_headers  # noqa: F401


def _account_id(client, admin_headers, username):
    accounts = client.get("/api/admin/accounts", headers=admin_headers).json()["accounts"]
    return next(a for a in accounts if a["username"] == username)


def test_profile_shows_and_saves_research_id_for_admins_too(client, register, admin_headers):
    headers = register("ridowner", research_id="101")
    assert client.get("/api/profile", headers=headers).json()["research_id"] == "101"

    saved = client.patch("/api/profile", headers=headers, json={"research_id": " 102 "})
    assert saved.status_code == 200 and saved.json()["research_id"] == "102"
    assert _account_id(client, admin_headers, "ridowner")["research_id"] == "102"


def test_profile_research_id_can_be_saved_with_other_fields(client, register):
    headers = register("ridmixed", research_id="201")
    saved = client.patch("/api/profile", headers=headers, json={"research_id": "202", "nickname": "新名"})
    assert saved.status_code == 200
    assert (saved.json()["research_id"], saved.json()["nickname"]) == ("202", "新名")


def test_profile_research_id_must_be_three_digits_unique_and_not_cleared(client, register):
    register("ridfirst", research_id="301")
    headers = register("ridsecond", research_id="302")
    for bad in ["P001", "01", "0001", "", None, "１２３"]:
        assert client.patch("/api/profile", headers=headers, json={"research_id": bad}).status_code == 422
    taken = client.patch("/api/profile", headers=headers, json={"research_id": "301"})
    assert taken.status_code == 409 and taken.json()["detail"] == "被试编号已被使用"
    assert client.get("/api/profile", headers=headers).json()["research_id"] == "302"


def test_profile_adds_research_id_for_an_account_without_one(client, register, admin_headers):
    headers = register("ridmissing", research_id="401")
    account = _account_id(client, admin_headers, "ridmissing")
    assert client.put(f"/api/admin/accounts/{account['id']}/research-id", headers=admin_headers,
                      json={"research_id": None}).json()["research_id"] is None
    assert client.get("/api/profile", headers=headers).json()["research_id"] is None
    assert client.patch("/api/profile", headers=headers, json={"research_id": "402"}).json()["research_id"] == "402"
    assert _account_id(client, admin_headers, "ridmissing")["research_id"] == "402"
