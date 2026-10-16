"""Existing-account migration, rollback and V2 nickname editing regressions."""
import asyncio
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text, inspect, select
from app.db import Base
from app.schemas import RegisterRequest, ProfileUpdate, ProfileOut, AccountInfo, AdminAccountItem

spec = importlib.util.spec_from_file_location('remove_tags', Path(__file__).parents[1]/'scripts/remove_public_tags_1012.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_new_schema_and_api_have_no_tag_contract():
    assert 'account_handles' not in Base.metadata.tables
    for schema in (RegisterRequest, ProfileUpdate, ProfileOut, AccountInfo, AdminAccountItem):
        assert not {'tag', 'display_id'}.intersection(schema.model_fields)


def test_existing_tags_backup_remove_and_restore(tmp_path):
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE user_accounts (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, profile_uuid TEXT)'))
        conn.execute(text("INSERT INTO user_accounts VALUES (1, 'olduser', 'unchanged-hash', 'profile-a')"))
        conn.execute(text('CREATE TABLE user_profile (uuid TEXT PRIMARY KEY, nickname TEXT)'))
        conn.execute(text("INSERT INTO user_profile VALUES ('profile-a', '老用户')"))
        conn.execute(text('CREATE TABLE account_handles (account_id INTEGER PRIMARY KEY REFERENCES user_accounts(id), base_username TEXT NOT NULL, normalized_base TEXT NOT NULL, tag VARCHAR(5) NOT NULL, created_at DATETIME, UNIQUE(normalized_base, tag))'))
        conn.execute(text('CREATE INDEX ix_account_handle_base ON account_handles(normalized_base)'))
        conn.execute(text("INSERT INTO account_handles VALUES (1, '老用户', '老用户', '12345', '2026-10-01 00:00:00.000000')"))
        before = migration.invariants(conn)
        assert migration.migrate(conn)['changed'] is False
        backup = tmp_path/'backup.json'
        assert migration.migrate(conn, backup=backup, apply=True)['removed_rows'] == 1
        assert not inspect(conn).has_table('account_handles')
        assert migration.invariants(conn) == before
        assert migration.migrate(conn, backup=backup, apply=True)['changed'] is False
        assert migration.restore(conn, backup)['restored_rows'] == 1
        assert conn.execute(text('SELECT tag FROM account_handles')).scalar_one() == '12345'
        assert migration.invariants(conn) == before
        # Never overwrite the only rollback snapshot or drop without a backup.
        with pytest.raises(FileExistsError):
            migration.migrate(conn, backup=backup, apply=True)
        assert inspect(conn).has_table('account_handles')
    engine.dispose()


def test_v2_nickname_patch_works_without_handle(monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database_v2_schema import metadata
    from app import v2_profile
    from app.models import UserAccount, AccountSettings
    from app.account_identity import audit_identity
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "database_schema_version", "v2")
    async def run():
        engine = create_async_engine('sqlite+aiosqlite://')
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            for number in (1, 2):
                uid = f'profile-{number}'
                await db.execute(metadata.tables['user_profile'].insert(), {'uuid': uid, 'nickname': '同名'})
                db.add(UserAccount(id=number, username=f'user{number}', password_hash='existing-hash', profile_uuid=uid))
                db.add(AccountSettings(account_id=number))
            await db.commit()
            result = await v2_profile.patch(db, 'profile-1', ProfileUpdate(nickname='  更新昵称  '))
            assert result.nickname == '更新昵称'
            assert 'tag' not in result.model_dump()
            await v2_profile.patch(db, 'profile-2', ProfileUpdate(nickname='更新昵称'))
            account = await db.get(UserAccount, 1)
            assert await audit_identity(db, account) == 'user1'
            assert account.profile_uuid == 'profile-1' and account.password_hash == 'existing-hash'
            assert (await v2_profile.read(db, 'profile-2')).nickname == '更新昵称'
        await engine.dispose()
    asyncio.run(run())


def test_old_account_sessions_and_login_survive_removal(client, register, db_sessionmaker, tmp_path):
    headers = register(username='oldlogin', nickname='旧账号')
    original = client.get('/api/auth/me', headers=headers).json()
    async def migrate_existing():
        from app.models import UserAccount
        async with db_sessionmaker() as db:
            account = (await db.execute(select(UserAccount).where(UserAccount.username == 'oldlogin'))).scalar_one()
            conn = await db.connection()
            await conn.execute(text('CREATE TABLE account_handles (account_id INTEGER PRIMARY KEY, base_username TEXT, normalized_base TEXT, tag VARCHAR(5), created_at DATETIME)'))
            await conn.execute(text("INSERT INTO account_handles VALUES (:id, '旧账号', '旧账号', '67890', NULL)"), {'id': account.id})
            await conn.run_sync(lambda c: migration.migrate(c, backup=tmp_path/'old-account.json', apply=True))
            await db.commit()
    asyncio.run(migrate_existing())
    assert client.get('/api/auth/me', headers=headers).json() == original
    login = client.post('/api/auth/login', json={'username':'oldlogin', 'password':'correct-horse-battery'})
    assert login.status_code == 200
    assert login.json()['account']['profile_uuid'] == original['profile_uuid']
    assert login.json()['account']['nickname'] == '旧账号'
