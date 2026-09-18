"""Birthday registration and targeted legacy-age confirmation, isolated only."""
import asyncio
from datetime import date
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models import UserAccount, AuthSession
from app.models_business import UserProfile
from app.birth_dates import age_on, today


def registration(**extra):
    return {"username": "birthcheck", "password": "local-birthday-password",
            "nickname": "Birthday Test", "tag": "91517", "email": "birthcheck@example.com", **extra}


@pytest.mark.parametrize("extra", [{}, {"age": 28}, {"birth_date": None}, {"birth_date": ""},
    {"birth_date": True}, {"birth_date": 1000000}, {"birth_date": "1999-02-29"},
    {"birth_date": "2999-01-01"}, {"birth_date": "1800-01-01"}, {"birth_date": "2025-01-01"},
    {"birth_date": "2000-01-01T00:00:00Z"}])
def test_bad_birthday_creates_nothing(client, db_sessionmaker, extra):
    response = client.post("/api/auth/register", json=registration(**extra))
    assert response.status_code == 422
    assert any(e["loc"] == ["body", "birth_date"] for e in response.json()["detail"])
    async def counts():
        async with db_sessionmaker() as db:
            for model in (UserAccount, AuthSession, UserProfile):
                assert await db.scalar(select(func.count()).select_from(model)) == 0
    asyncio.run(counts())


def test_age_boundaries_and_leap_birthday():
    assert age_on(date(2000, 9, 18), date(2026, 9, 17)) == 25
    assert age_on(date(2000, 9, 18), date(2026, 9, 18)) == 26
    assert age_on(date(2000, 2, 29), date(2025, 2, 28)) == 24
    assert age_on(date(2000, 2, 29), date(2025, 3, 1)) == 25


@pytest.mark.parametrize("age", [10, 37, 120])
def test_birthday_saved_and_new_ten_year_old_not_prompted(client, age):
    birthday = date(today().year-age, 1, 1)
    result = client.post("/api/auth/register", json=registration(birth_date=birthday.isoformat()))
    assert result.status_code == 201
    assert result.json()["account"]["birth_date_required"] is False
    headers = {"Authorization": f"Bearer {result.json()['token']}"}
    profile = client.get("/api/profile", headers=headers).json()
    assert profile["birth_date"] == birthday.isoformat() and profile["age"] == age


@pytest.mark.parametrize("old_age, required", [(10, True), (28, False), (None, False)])
def test_targeted_prompt_persists_until_confirmed(client, db_sessionmaker, old_age, required):
    result = client.post("/api/auth/register", json=registration(birth_date="1990-06-15"))
    assert result.status_code == 201
    async def historical_profile():
        async with db_sessionmaker() as db:
            await db.execute(update(UserProfile).values(age=old_age, birth_date=None))
            await db.commit()
    asyncio.run(historical_profile())
    login = client.post("/api/auth/login", json={"username": "birthcheck", "password": "local-birthday-password"}).json()
    assert login["account"]["birth_date_required"] is required
    headers = {"Authorization": f"Bearer {login['token']}"}
    assert client.get("/api/auth/me", headers=headers).json()["birth_date_required"] is required
    assert client.put("/api/auth/birth-date", headers=headers, json={"birth_date": "2999-01-01"}).status_code == 422
    assert client.get("/api/auth/me", headers=headers).json()["birth_date_required"] is required
    done = client.put("/api/auth/birth-date", headers=headers, json={"birth_date": "1990-06-15"})
    assert done.status_code == 200 and done.json()["birth_date_required"] is False
    profile = client.get("/api/profile", headers=headers).json()
    assert profile["birth_date"] == "1990-06-15" and profile["age"] == age_on(date(1990,6,15))
    assert client.get("/api/auth/me", headers=headers).json()["birth_date_required"] is False
    assert client.patch("/api/profile", headers=headers, json={"birth_date": None}).status_code == 422
    assert client.patch("/api/profile", headers=headers, json={"age": 10}).status_code == 422


def test_birthday_endpoint_requires_login(client):
    assert client.put("/api/auth/birth-date", json={"birth_date":"1990-06-15"}).status_code == 401


@pytest.mark.asyncio
async def test_v2_registration_and_confirmation(monkeypatch):
    from app.config import get_settings
    from app.database_v2_schema import metadata
    from app.db import Base, get_db
    from app.main import app
    monkeypatch.setattr(get_settings(), "database_schema_version", "v2")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def database():
        async with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post("/api/auth/register", json=registration(birth_date="1990-06-15"))
            assert result.status_code == 201, result.text
            headers = {"Authorization": f"Bearer {result.json()['token']}"}
            table = metadata.tables["user_profile"]
            async with sessions() as db:
                profile = (await db.execute(select(table))).mappings().one()
                assert profile["birth_date"] == date(1990,6,15) and profile["birth_year"] == 1990
                assert profile["age_reported_at"] is not None
                await db.execute(update(table).values(birth_date=None, reported_age=10))
                await db.commit()
            assert (await client.get("/api/auth/me", headers=headers)).json()["birth_date_required"] is True
            done = await client.put("/api/auth/birth-date", headers=headers, json={"birth_date":"1988-02-29"})
            assert done.status_code == 200, done.text
            assert done.json()["birth_date_required"] is False
            profile = (await client.get("/api/profile", headers=headers)).json()
            assert profile["birth_date"] == "1988-02-29" and profile["age"] == age_on(date(1988,2,29))
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_migration_is_explicit_additive_and_idempotent(tmp_path):
    from scripts.add_birth_date_0917 import migrate
    path = (tmp_path / "birthday.db").as_posix()
    url = "sqlite+aiosqlite:///" + path
    engine = create_async_engine(url)
    async with engine.begin() as db:
        await db.execute(text("CREATE TABLE user_profile(uuid TEXT PRIMARY KEY, reported_age INTEGER)"))
        await db.execute(text("INSERT INTO user_profile VALUES ('historic', 10)"))
    with pytest.raises(RuntimeError):
        await migrate(url, expected_database="wrong", apply=True)
    await migrate(url, expected_database=path)
    async with engine.connect() as db:
        assert "birth_date" not in [row[1] for row in await db.execute(text("PRAGMA table_info(user_profile)"))]
    await migrate(url, expected_database=path, apply=True)
    await migrate(url, expected_database=path, apply=True)
    async with engine.connect() as db:
        assert (await db.execute(text("SELECT reported_age,birth_date FROM user_profile"))).one() == (10, None)
    await engine.dispose()
