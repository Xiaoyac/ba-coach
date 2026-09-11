"""Offline workbench regression: real HTTP/graph, fake model, isolated SQLite."""
import asyncio
import base64
from datetime import timedelta
from io import BytesIO
import uuid

from openpyxl import Workbook
import pytest
from sqlalchemy import func, select

from app.models import AccountHandle, AccountSettings, Conversation, _utcnow
from app.models_business import BizBase
from app.providers.base import ProviderError, as_text
from app.retrieval import StubKnowledgeBase
from app.routes import admin_evaluations as route
from app.test_workbench import EvaluationCase, EvaluationRun, HEADERS, ImportRequest, export_csv, parse_upload, run_data

ROOT = "/api/admin/evaluations"
CASE = {"case_code": "M1-001", "module": "module_1", "user_type": "愿意尝试行动",
        "scenario": "虚构用户最近减少活动", "user_input": "我最近总是提不起劲，可以怎么开始？",
        "expected_behavior": "EXPECTED_ONLY_FOR_REVIEW_9X", "extra_columns": {"负责人": "测试同事"}}


@pytest.fixture
def admin_headers(register, db_sessionmaker, monkeypatch, provider):
    headers = register(username="testadmin", nickname="Test Admin")
    async def promote():
        async with db_sessionmaker() as db:
            row = (await db.execute(select(AccountSettings).join(AccountHandle,
                AccountHandle.account_id == AccountSettings.account_id).where(AccountHandle.normalized_base == "test admin"))).scalar_one()
            row.role = "admin"
            await db.commit()
    asyncio.run(promote())
    monkeypatch.setattr(route, "get_provider", lambda name=None: provider)
    monkeypatch.setattr(route, "get_knowledge_base", StubKnowledgeBase)
    return headers


def make_case(client, headers, **changes):
    response = client.post(ROOT + "/cases", headers=headers, json={**CASE, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def run_case(client, headers, case, **changes):
    payload = {"request_id": str(uuid.uuid4()), "provider": "deepseek", **changes}
    response = client.post(f"{ROOT}/cases/{case['id']}/runs", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    return client.get(f"{ROOT}/runs/{payload['request_id']}", headers=headers).json()


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/cases", None), ("POST", "/cases", CASE), ("PUT", "/cases/missing", {**CASE, "revision": 1}),
    ("GET", "/runs", None), ("GET", "/runs/missing", None), ("GET", "/cases.csv", None), ("GET", "/results.csv", None),
    ("POST", "/import/preview", {"filename":"a.csv", "content_base64":""}),
    ("POST", "/import", {"cases":[CASE]}),
    ("POST", "/cases/missing/runs", {"request_id":str(uuid.uuid4()), "provider":"deepseek"}),
    ("POST", "/runs/missing/reviews", {"verdict":"pass"}),
])
def test_admin_only(client, auth_headers, method, path, body):
    assert client.request(method, ROOT + path, json=body, headers=auth_headers).status_code == 403
    assert client.request(method, ROOT + path, json=body).status_code == 401


def test_case_revision_and_import_are_atomic(client, admin_headers):
    case = make_case(client, admin_headers)
    assert client.post(ROOT + "/cases", headers=admin_headers, json=CASE).status_code == 409
    edit = {**CASE, "revision": 1, "scenario": "新场景"}
    response = client.put(f"{ROOT}/cases/{case['id']}", headers=admin_headers, json=edit)
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert client.put(f"{ROOT}/cases/{case['id']}", headers=admin_headers, json=edit).status_code == 409
    response = client.post(ROOT + "/import", headers=admin_headers, json={"cases":[{**CASE,"case_code":"M2-NEW"},CASE]})
    assert response.status_code == 409
    assert len(client.get(ROOT + "/cases", headers=admin_headers).json()["cases"]) == 1
    response = client.post(ROOT + "/import", headers=admin_headers, json={"cases":[{**CASE,"case_code":"M2-NEW"}]})
    assert response.status_code == 201 and response.json()["imported"] == 1


def test_execution_snapshot_followup_and_no_business_writes(client, admin_headers, provider, db_sessionmaker):
    async def counts():
        async with db_sessionmaker() as db:
            return [await db.scalar(select(func.count()).select_from(table))
                    for table in [Conversation.__table__, *BizBase.metadata.sorted_tables]]
    before = asyncio.run(counts())
    case = make_case(client, admin_headers)
    assert client.put("/api/admin/prompts/module_1", headers=admin_headers, json={"content":"CUSTOM_MODULE_SNAPSHOT"}).status_code == 200
    run = run_case(client, admin_headers, case)
    assert run["status"] == "completed", run
    assert run["reply"] and run["model"] == "stub-1" and len(run["transcript"]) == 2
    assert run["metrics"]["duration_ms"] >= 0 and len(run["metrics"]["prompt_sha256"]) == 64
    assert "CUSTOM_MODULE_SNAPSHOT" in as_text(provider.systems[-1])
    assert CASE["expected_behavior"] not in str(provider.seen) + str(provider.systems) + str(provider.route_calls)
    assert CASE["scenario"] in as_text(provider.systems[-1])
    assert "prompt_snapshot" not in run and "memory" not in run
    assert client.put(f"{ROOT}/cases/{case['id']}", headers=admin_headers,
        json={**CASE, "revision":1, "module":"module_2", "user_input":"changed"}).status_code == 200
    client.put("/api/admin/prompts/module_1", headers=admin_headers, json={"content":"NEW_PROMPT"})
    child = run_case(client, admin_headers, case, parent_run_id=run["id"], message="今天可以先做五分钟吗？")
    assert child["status"] == "completed" and len(child["transcript"]) == 4
    assert child["case_snapshot"]["revision"] == 1 and child["case_snapshot"]["module"] == "module_1"
    assert child["metrics"]["prompt_sha256"] == run["metrics"]["prompt_sha256"]
    assert len(provider.seen[-1]) == 3
    assert "CUSTOM_MODULE_SNAPSHOT" in as_text(provider.systems[-1])
    assert asyncio.run(counts()) == before


def test_idempotency_and_append_only_reviews(client, admin_headers, provider):
    case = make_case(client, admin_headers)
    request_id = str(uuid.uuid4())
    run = run_case(client, admin_headers, case, request_id=request_id)
    calls = len(provider.seen)
    again = run_case(client, admin_headers, case, request_id=request_id)
    assert again["id"] == run["id"] and len(provider.seen) == calls
    assert client.post(f"{ROOT}/cases/{case['id']}/runs", headers=admin_headers,
        json={"request_id": request_id, "provider":"claude"}).status_code == 409
    for verdict in ["fail", "pass"]:
        assert client.post(f"{ROOT}/runs/{run['id']}/reviews", headers=admin_headers,
            json={"verdict":verdict, "notes":f"review {verdict}"}).status_code == 201
    detail = client.get(f"{ROOT}/runs/{run['id']}", headers=admin_headers).json()
    assert [r["verdict"] for r in detail["reviews"]] == ["pass", "fail"]
    assert client.get(ROOT + "/runs", headers=admin_headers).json()["runs"][0]["latest_verdict"] == "pass"
    export = client.get(ROOT + "/results.csv", headers=admin_headers)
    assert export.status_code == 200 and "review pass" in export.text and "review fail" not in export.text


@pytest.mark.parametrize("error,code", [(ProviderError,"provider_error"), (RuntimeError,"execution_failed"), (TimeoutError,"timeout")])
def test_error_record_and_validation(client, admin_headers, provider, error, code):
    case = make_case(client, admin_headers)
    provider.fail_with = error("do-not-expose-secret")
    run = run_case(client, admin_headers, case)
    assert run["status"] == "failed" and run["error_code"] == code
    assert "do-not-expose-secret" not in str(run)
    assert client.post(f"{ROOT}/runs/{run['id']}/reviews", headers=admin_headers, json={"verdict":"pass"}).status_code == 422
    assert client.post(f"{ROOT}/cases/{case['id']}/runs", headers=admin_headers,
        json={"request_id":str(uuid.uuid4()),"provider":"deepseek","parent_run_id":run["id"],"message":"next"}).status_code == 422
    assert client.post(f"{ROOT}/cases/{case['id']}/runs", headers=admin_headers,
        json={"request_id":str(uuid.uuid4()),"provider":"deepseek","message":"override"}).status_code == 422


def upload(rows, xlsx=False):
    if xlsx:
        book = Workbook(); book.active.title = "评测集"
        for row in rows: book.active.append(row)
        buf = BytesIO(); book.save(buf); raw = buf.getvalue()
    else:
        raw = export_csv(rows)
    return ImportRequest(filename="test.xlsx" if xlsx else "test.csv", content_base64=base64.b64encode(raw).decode())


@pytest.mark.parametrize("xlsx", [False, True])
def test_import_screenshot_columns_roundtrip(xlsx):
    rows = [HEADERS[:2] + ["用户类型（不一定每个模块都出现）"] + HEADERS[3:] + ["负责人"],
            ["M1-001","M1","积极型","情境","第一行\n第二行","期望","同事甲"], [],
            ["M2-001","M2","低动力","情境2","input","期望2","同事乙"]]
    parsed = parse_upload(upload(rows,xlsx))
    assert not parsed["errors"] and len(parsed["cases"]) == 2
    assert parsed["cases"][0]["module"] == "module_1"
    assert parsed["cases"][0]["user_input"] == "第一行\n第二行"
    assert parsed["cases"][0]["extra_columns"] == {"负责人":"同事甲"}


def test_import_rejects_formula_duplicates_invalid_module_and_missing_header():
    with pytest.raises(ValueError, match="公式"):
        parse_upload(upload([HEADERS,["A","M1","","","=1+1",""]],True))
    with pytest.raises(ValueError, match="缺少列"):
        parse_upload(upload([["Case ID"],["A"]]))
    result = parse_upload(upload([HEADERS,["A","M1","","","input",""],["A","M1","","","input",""],["B","M7","","","input",""]]))
    assert len(result["cases"]) == 1 and [e["row"] for e in result["errors"]] == [3,4]
    with pytest.raises(ValueError, match="无法读取"):
        parse_upload(ImportRequest(filename="bad.xlsx",content_base64=base64.b64encode(b"broken").decode()))
    with pytest.raises(ValueError, match="编码无效"):
        parse_upload(ImportRequest(filename="bad.csv",content_base64="!"))


def test_preview_and_export_endpoint(client, admin_headers):
    payload = upload([HEADERS,["M4-001","M4","回顾","运动记录","本周走了三次","总结"]])
    response = client.post(ROOT + "/import/preview",headers=admin_headers,json=payload.model_dump())
    assert response.status_code == 200 and response.json()["cases"][0]["module"] == "module_4"
    make_case(client,admin_headers,user_input="=cmd|' /C calc'!A0")
    response = client.get(ROOT + "/cases.csv",headers=admin_headers)
    assert response.content.startswith(b"\xef\xbb\xbf") and "'=cmd" in response.text
    assert "负责人" in response.text


def test_risk_branch_still_runs(client, admin_headers, provider):
    case = make_case(client, admin_headers)
    provider.risk_result = '{"risk_status": 1, "risk_expression_type": 2}'
    run = run_case(client, admin_headers, case)
    assert run["status"] == "completed" and run["metrics"]["risk_flagged"] is True
    assert not run["metrics"]["retrieved"]


def test_job_claim_does_not_call_model_twice(client, admin_headers, provider, db_sessionmaker):
    case = make_case(client, admin_headers)
    run = run_case(client, admin_headers, case)
    calls = len(provider.seen)
    asyncio.run(route.execute_run(run["id"], db_sessionmaker))
    assert len(provider.seen) == calls


def test_followup_limit(client, admin_headers, db_sessionmaker):
    case = make_case(client, admin_headers)
    run = run_case(client, admin_headers, case)
    async def fill_history():
        async with db_sessionmaker() as db:
            row = await db.get(EvaluationRun, run["id"])
            row.transcript = [{"role":"user", "content":"hi"}, {"role":"assistant","content":"hello"}] * 20
            await db.commit()
    asyncio.run(fill_history())
    response = client.post(f"{ROOT}/cases/{case['id']}/runs", headers=admin_headers,
        json={"request_id":str(uuid.uuid4()),"provider":"deepseek","parent_run_id":run["id"],"message":"next"})
    assert response.status_code == 422 and "20" in response.text


def test_active_run_limit(client, admin_headers, monkeypatch):
    async def paused_worker(*args):
        pass
    monkeypatch.setattr(route,"execute_run",paused_worker)
    case = make_case(client,admin_headers)
    for _ in range(3):
        run = run_case(client,admin_headers,case)
        assert run["status"] == "queued"
    response = client.post(f"{ROOT}/cases/{case['id']}/runs",headers=admin_headers,
        json={"request_id":str(uuid.uuid4()),"provider":"deepseek"})
    assert response.status_code == 429


@pytest.mark.parametrize("status", ["queued","running"])
def test_stale_jobs_are_visible_as_interrupted(status):
    row = EvaluationRun(id="test",case_id="case",provider="deepseek",status=status,case_snapshot={},
        input_text="hi",reply="",transcript=[],metrics={},created_at=_utcnow()-timedelta(seconds=241))
    assert run_data(row)["status"] == "interrupted"


@pytest.mark.asyncio
async def test_scoped_migration_dry_run_apply_and_idempotency(tmp_path):
    from sqlalchemy import inspect
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.db import Base
    from scripts.create_evaluation_tables import migrate, TABLES
    path = (tmp_path / "migration.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[Base.metadata.tables["user_accounts"]]))
        report = await migrate(engine,path)
        assert len(report["missing_before"]) == 3
        async with engine.connect() as conn:
            assert await conn.run_sync(lambda c: inspect(c).get_table_names()) == ["user_accounts"]
        await migrate(engine,path,True)
        report = await migrate(engine,path,True)
        assert not report["missing_before"]
        async with engine.connect() as conn:
            assert set(await conn.run_sync(lambda c: inspect(c).get_table_names())) == {"user_accounts", *(t.name for t in TABLES)}
        with pytest.raises(ValueError, match="does not match"):
            await migrate(engine,"wrong",True)
    finally:
        await engine.dispose()
