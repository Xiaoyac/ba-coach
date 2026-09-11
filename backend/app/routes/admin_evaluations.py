"""Shared admin workbench. Runs are isolated from real programme records."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import logging
from time import perf_counter
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db, get_sessionmaker
from ..graph.builder import get_graph
from ..graph.state import GraphContext
from ..identity import CallerIdentity, require_admin
from ..models import _utcnow
from ..prompt_store import effective_prompt_pair, effective_mediator_prompt
from ..providers import get_provider
from ..retrieval import get_knowledge_base
from ..schemas import Message
from ..session import InMemorySessionStore
from ..test_workbench import (
    CaseInput, CaseUpdate, EvaluationCase, EvaluationReview, EvaluationRun, FIELDS, HEADERS,
    ImportCommit, ImportRequest, ReviewInput, RunRequest, case_data, export_csv, parse_upload, run_data,
)

router = APIRouter(prefix="/admin/evaluations", tags=["admin-evaluations"])
logger = logging.getLogger(__name__)


async def get_case(db, case_id):
    row = await db.get(EvaluationCase, case_id)
    if row is None:
        raise HTTPException(404, "测试用例不存在")
    return row


@router.get("/cases")
async def list_cases(caller=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(EvaluationCase).order_by(EvaluationCase.case_code).limit(2000))).scalars().all()
    return {"cases": [case_data(r) for r in rows], "limit": 2000}


@router.post("/cases", status_code=201)
async def create_case(payload: CaseInput, caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = EvaluationCase(**payload.model_dump(), created_by=caller.account.id)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Case ID 已存在，请使用其他编号") from None
    return case_data(row)


@router.put("/cases/{case_id}")
async def edit_case(case_id: str, payload: CaseUpdate, caller=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    await get_case(db, case_id)
    try:
        result = await db.execute(update(EvaluationCase).where(EvaluationCase.id == case_id,
            EvaluationCase.revision == payload.revision).values(**payload.model_dump(exclude={"revision"}),
                revision=payload.revision + 1, updated_at=_utcnow()))
        if result.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, "用例已被其他同事修改，请刷新后重试")
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Case ID 已存在") from None
    db.expire_all()
    return case_data(await get_case(db, case_id))


@router.post("/import/preview")
async def preview_import(payload: ImportRequest, caller=Depends(require_admin)):
    try:
        return await asyncio.to_thread(parse_upload, payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/import", status_code=201)
async def commit_import(payload: ImportCommit, caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    codes = [c.case_code.casefold() for c in payload.cases]
    if len(codes) != len(set(codes)):
        raise HTTPException(422, "导入内容包含重复 Case ID")
    rows = [EvaluationCase(**c.model_dump(), created_by=caller.account.id) for c in payload.cases]
    db.add_all(rows)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "存在同编号用例，本次导入全部取消；请修改编号或编辑已有用例") from None
    return {"imported": len(rows)}


@router.get("/cases.csv")
async def download_cases(caller=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(EvaluationCase).order_by(EvaluationCase.case_code))).scalars().all()
    extras = sorted({key for row in rows for key in row.extra_columns})
    data = [HEADERS + extras]
    for row in rows:
        data.append([getattr(row, key).replace("module_", "M") if key == "module" else getattr(row, key) for key in FIELDS]
                    + [row.extra_columns.get(k, "") for k in extras])
    return Response(export_csv(data), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="evaluation-cases.csv"'})


async def execute_run(run_id, factory):
    """Persisted claim prevents duplicate invocation; interruption stays visible and is not auto-retried."""
    async with factory() as db:
        claim = await db.execute(update(EvaluationRun).where(EvaluationRun.id == run_id,
            EvaluationRun.status == "queued").values(status="running"))
        await db.commit()
        if claim.rowcount != 1:
            return
        row = await db.get(EvaluationRun, run_id)
        snapshot, prompts = row.case_snapshot, row.prompt_snapshot
        provider_name, text = row.provider, row.input_text
        transcript, memory = row.transcript, row.memory
        await db.commit()
    started = perf_counter()
    values = {"status": "failed", "error_code": "execution_failed"}
    try:
        settings = get_settings()
        store = InMemorySessionStore(ttl_seconds=600, max_messages=40)
        session = await store.adopt(str(uuid.uuid4()), [Message(**m) for m in transcript], snapshot["module"],
                                    {**memory, "sandbox_mode": "true"})
        # These fields describe the synthetic user. Expected outcomes stay exclusively in the review UI.
        session.metadata.update({"测试情境（虚构用户）": snapshot["scenario"], "用户类型（虚构）": snapshot["user_type"]})
        context = GraphContext(provider=get_provider(provider_name), router_provider=get_provider("deepseek"),
            store=store, knowledge_base=get_knowledge_base(), settings=settings,
            sessionmaker=None, memos=None, stream=False, prompt_snapshot=prompts)
        final = await asyncio.wait_for(get_graph().ainvoke(
            {"session_id": session.session_id, "user_input": text, "forced_module": snapshot["module"]}, context=context), timeout=180)
        metrics = {"telemetry": final.get("telemetry", {}), "usage": final.get("usage", {}),
                   "retrieved": [{"id": h.id, "source": h.source, "score": h.score} for h in final.get("retrieved_knowledge", [])],
                   "risk_flagged": bool(final.get("risk")),
                   "knowledge_intent_gate_enabled": settings.knowledge_intent_gate_enabled,
                   "risk_gate_enabled": settings.risk_gate_enabled,
                   "prompt_sha256": hashlib.sha256((prompts["global"] + prompts[snapshot["module"]]).encode()).hexdigest()}
        values = {"status": "failed" if final.get("error") else "completed",
                  "error_code": "provider_error" if final.get("error") else None,
                  "reply": final.get("final_response", ""), "model": final.get("model"),
                  "transcript": transcript + [{"role": "user", "content": text}, {"role": "assistant", "content": final.get("final_response", "")}],
                  "memory": final.get("memory", {}), "metrics": metrics}
    except TimeoutError:
        values["error_code"] = "timeout"
    except asyncio.CancelledError:
        values["error_code"] = "interrupted"
        values["status"] = "interrupted"
    except Exception:
        logger.exception("test execution failed: %s", run_id)
    values.setdefault("metrics", {})["duration_ms"] = round((perf_counter() - started) * 1000)
    values["finished_at"] = _utcnow()
    async with factory() as db:
        await db.execute(update(EvaluationRun).where(EvaluationRun.id == run_id).values(**values))
        await db.commit()


@router.post("/cases/{case_id}/runs", status_code=202)
async def start_run(case_id: str, payload: RunRequest, tasks: BackgroundTasks,
                    caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    case = await get_case(db, case_id)
    existing = await db.get(EvaluationRun, str(payload.request_id))
    if existing:
        if (existing.case_id != case_id or existing.created_by != caller.account.id
                or existing.provider != payload.provider or existing.parent_run_id != (str(payload.parent_run_id) if payload.parent_run_id else None)
                or (payload.message is not None and payload.message != existing.input_text)):
            raise HTTPException(409, "请求编号已用于另一项执行")
        return run_data(existing)
    count = await db.scalar(select(func.count()).select_from(EvaluationRun).where(
        EvaluationRun.created_by == caller.account.id, EvaluationRun.status.in_(("queued", "running")),
        EvaluationRun.created_at > _utcnow() - timedelta(seconds=240)))
    if count >= 3:
        raise HTTPException(429, "已有运行中的测试，请等待完成")
    transcript, memory = [], {}
    snapshot = case_data(case)
    global_prompt, module_prompt = await effective_prompt_pair(db, case.module)
    prompts = {"global": global_prompt, case.module: module_prompt,
               "knowledge_mediator": await effective_mediator_prompt(db)}
    text = case.user_input
    if payload.parent_run_id:
        parent = await db.get(EvaluationRun, str(payload.parent_run_id))
        if not parent or parent.case_id != case_id or parent.status != "completed":
            raise HTTPException(422, "追问必须选择当前用例已完成的运行")
        if not payload.message or not payload.message.strip():
            raise HTTPException(422, "请输入追问内容")
        if len(parent.transcript) >= 40:
            raise HTTPException(422, "一次测试最多 20 轮，请新建运行")
        snapshot, prompts = parent.case_snapshot, parent.prompt_snapshot
        transcript, memory, text = parent.transcript, parent.memory, payload.message.strip()
    elif payload.message is not None:
        raise HTTPException(422, "首轮输入来自保存的用例；修改输入请先编辑用例")
    row = EvaluationRun(id=str(payload.request_id), case_id=case_id,
        parent_run_id=str(payload.parent_run_id) if payload.parent_run_id else None,
        created_by=caller.account.id, status="queued", case_snapshot=snapshot,
        prompt_snapshot=prompts, provider=payload.provider, input_text=text, transcript=transcript, memory=memory)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "请求已提交，请刷新运行记录") from None
    tasks.add_task(execute_run, row.id, get_sessionmaker())
    return run_data(row)


@router.get("/runs")
async def list_runs(case_id: str | None = None, offset: int = Query(default=0, ge=0),
                    caller=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    statement = select(EvaluationRun)
    if case_id:
        statement = statement.where(EvaluationRun.case_id == case_id)
    rows = (await db.execute(statement.order_by(EvaluationRun.created_at.desc()).offset(offset).limit(100))).scalars().all()
    reviews = (await db.execute(select(EvaluationReview).where(EvaluationReview.run_id.in_([r.id for r in rows]))
        .order_by(EvaluationReview.created_at))).scalars().all() if rows else []
    latest = {review.run_id: review.verdict for review in reviews}
    return {"runs": [{**run_data(row), "latest_verdict": latest.get(row.id, "unreviewed")} for row in rows],
            "next_offset": offset + 100 if len(rows) == 100 else None}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, caller=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(EvaluationRun, run_id)
    if row is None:
        raise HTTPException(404, "运行不存在")
    reviews = (await db.execute(select(EvaluationReview).where(EvaluationReview.run_id == run_id)
                               .order_by(EvaluationReview.created_at.desc()))).scalars().all()
    return {**run_data(row), "reviews": [{"id": r.id, "reviewer_id": r.reviewer_id, "verdict": r.verdict,
            "notes": r.notes, "created_at": r.created_at.isoformat()} for r in reviews]}


@router.post("/runs/{run_id}/reviews", status_code=201)
async def review_run(run_id: str, payload: ReviewInput, caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(EvaluationRun, run_id)
    if row is None:
        raise HTTPException(404, "运行不存在")
    if row.status != "completed" and payload.verdict != "unreviewed":
        raise HTTPException(422, "未完成的运行不能标为通过或未通过")
    review = EvaluationReview(run_id=run_id, reviewer_id=caller.account.id, **payload.model_dump())
    db.add(review)
    await db.commit()
    return {"id": review.id}


@router.get("/results.csv")
async def download_results(caller=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    runs = (await db.execute(select(EvaluationRun).order_by(EvaluationRun.created_at))).scalars().all()
    reviews = (await db.execute(select(EvaluationReview).order_by(EvaluationReview.created_at))).scalars().all()
    latest = {r.run_id: r for r in reviews}
    data = [HEADERS + ["运行ID", "父运行ID", "用例版本", "实际输入", "实际回答", "模型", "运行状态", "总耗时ms", "评审结论", "评审备注", "测试时间UTC"]]
    for row in runs:
        review = latest.get(row.id)
        data.append([row.case_snapshot[k].replace("module_", "M") if k == "module" else row.case_snapshot[k] for k in FIELDS] + [row.id, row.parent_run_id or "", row.case_snapshot["revision"],
            row.input_text, row.reply, row.model or row.provider, run_data(row)["status"], row.metrics.get("duration_ms", ""),
            review.verdict if review else "unreviewed", review.notes if review else "", row.created_at.isoformat()])
    return Response(export_csv(data), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="evaluation-results.csv"'})
