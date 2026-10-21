"""Year-only registration, legacy profiles, prompt minimisation and migration."""
import asyncio
from datetime import date
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update, text, create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models import UserAccount
from app.models_business import UserProfile
from app.birth_years import age_from_year, today, validate_birth_year
from invitation_helpers import with_test_invitation


def registration(**extra):
    return {'username':'yearcheck','password':'year-only-test-password','nickname':'Year Test',
            'email':'yearcheck@example.com','research_id':'P-year','phone':'13800138000',**extra}


@pytest.mark.parametrize('extra',[{}, {'age':28},{'birth_year':None},{'birth_year':''},
    {'birth_year':True},{'birth_year':1998.5},{'birth_year':'1998-02-03'},
    {'birth_year':2999},{'birth_year':1800},{'birth_year':today().year-9},
    {'birth_date':'1998-02-03'},{'birth_year':1998,'birth_date':'1998-02-03'}])
def test_invalid_input_or_full_date_never_creates_account(client,db_sessionmaker,extra):
    r=client.post('/api/auth/register',json=with_test_invitation(client,registration(**extra)))
    assert r.status_code==422
    async def count():
        async with db_sessionmaker() as db:
            assert await db.scalar(select(UserAccount.id).where(UserAccount.username=='yearcheck')) is None
    asyncio.run(count())


@pytest.mark.parametrize('age',[10,26,120])
def test_registration_and_profile_only_have_year(client,age):
    year=today().year-age
    r=client.post('/api/auth/register',json=with_test_invitation(client,registration(birth_year=year)))
    assert r.status_code==201,r.text
    assert r.json()['account']['birth_year_required'] is False
    headers={'Authorization':'Bearer '+r.json()['token']}
    p=client.get('/api/profile',headers=headers).json()
    assert p['birth_year']==year and p['age']==age and 'birth_date' not in p
    assert client.patch('/api/profile',headers=headers,json={'birth_date':'2000-01-01'}).status_code==422
    assert client.put('/api/auth/birth-date',headers=headers,json={'birth_date':'2000-01-01'}).status_code==410
    changed=client.patch('/api/profile',headers=headers,json={'birth_year':2000})
    assert changed.status_code==200 and changed.json()['birth_year']==2000
    assert client.patch('/api/profile',headers=headers,json={'birth_year':None}).status_code==422


def test_year_boundaries_and_date_independent_estimate():
    assert age_from_year(2000,date(2026,1,1))==age_from_year(2000,date(2026,12,31))==26
    assert validate_birth_year(str(today().year-10))==today().year-10
    with pytest.raises(ValueError):validate_birth_year(today().year-121)


@pytest.mark.parametrize('reported_age',[10,28,None])
def test_legacy_missing_year_can_be_completed(client,db_sessionmaker,reported_age):
    r=client.post('/api/auth/register',json=with_test_invitation(client,registration(birth_year=1990)))
    headers={'Authorization':'Bearer '+r.json()['token']}
    async def unset():
        async with db_sessionmaker() as db:
            await db.execute(update(UserProfile).values(birth_year=None,age=reported_age));await db.commit()
    asyncio.run(unset())
    assert client.get('/api/auth/me',headers=headers).json()['birth_year_required'] is True
    r=client.put('/api/auth/birth-year',headers=headers,json={'birth_year':1988})
    assert r.status_code==200 and r.json()['birth_year_required'] is False
    assert client.put('/api/auth/birth-year',json={'birth_year':1988}).status_code==401


@pytest.mark.asyncio
async def test_v2_year_only_writes_and_prompt_do_not_include_birth_date(monkeypatch):
    from app.config import get_settings
    from app.database_v2_schema import metadata
    from app.db import Base,get_db
    from app.main import app
    from app.clinical_store import load_profile_context
    from app.models import RegistrationInvite
    monkeypatch.setattr(get_settings(),'database_schema_version','v2')
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all);await conn.run_sync(Base.metadata.create_all)
        assert 'birth_date' not in await conn.run_sync(lambda c:[r['name'] for r in inspect(c).get_columns('user_profile')])
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    async def database():
        async with sessions() as db:yield db
    app.dependency_overrides[get_db]=database
    try:
        async with sessions() as db:db.add(RegistrationInvite(code='C'*32));await db.commit()
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            r=await client.post('/api/auth/register',json={**registration(birth_year=1990),'invitation_code':'C'*32});assert r.status_code==201,r.text
            headers={'Authorization':'Bearer '+r.json()['token']};uid=r.json()['account']['profile_uuid']
            p=(await client.get('/api/profile',headers=headers)).json();assert p['birth_year']==1990 and 'birth_date' not in p
            changed=await client.put('/api/auth/birth-year',headers=headers,json={'birth_year':1988});assert changed.status_code==200
            async with sessions() as db:
                row=(await db.execute(select(metadata.tables['user_profile']))).mappings().one()
                assert row['birth_year']==1988 and row['reported_age']==age_from_year(1988)
            context='\n'.join(await load_profile_context(sessions,user_id=uid))
            assert '按出生年份估算' in context and 'birth_year' not in context and 'birth_date' not in context
    finally:
        app.dependency_overrides.pop(get_db,None);await engine.dispose()


@pytest.mark.parametrize('legacy',[True,False])
def test_two_phase_migration_preserves_new_year_edits_and_unknowns(tmp_path,legacy):
    from scripts.migrate_birth_year_only_1015 import migrate,restore
    engine=create_engine('sqlite:///:memory:')
    age='age' if legacy else 'reported_age'
    with engine.begin() as c:
        c.execute(text(f'CREATE TABLE user_profile(uuid TEXT PRIMARY KEY,birth_date DATE,{age} INTEGER'+('' if legacy else ',birth_year INTEGER')+')'))
        c.execute(text(f"INSERT INTO user_profile(uuid,birth_date,{age}) VALUES ('dated','1990-06-15',35),('unknown',NULL,10)"))
        before=tmp_path/'prepare.json';migrate(c,'prepare',before,current_year=2026)
        assert c.execute(text('SELECT birth_year FROM user_profile WHERE uuid="dated"')).scalar()==1990
        assert 'birth_date' in {r['name'] for r in inspect(c).get_columns('user_profile')}
        c.execute(text("UPDATE user_profile SET birth_year=1991 WHERE uuid='dated'"))
        c.execute(text(f"INSERT INTO user_profile(uuid,birth_year,{age}) VALUES ('new',2000,26)"))
        final=tmp_path/'finish.json';migrate(c,'finish',final,current_year=2026)
        assert 'birth_date' not in {r['name'] for r in inspect(c).get_columns('user_profile')}
        assert c.execute(text('SELECT birth_year FROM user_profile WHERE uuid="dated"')).scalar()==1991
        assert c.execute(text('SELECT birth_year FROM user_profile WHERE uuid="unknown"')).scalar() is None
        assert before.stat().st_mode&0o777==0o600
        with pytest.raises(FileExistsError):migrate(c,'finish',final,current_year=2026)
        restore(c,final)
        assert c.execute(text('SELECT birth_date FROM user_profile WHERE uuid="dated"')).scalar() is None
    engine.dispose()
