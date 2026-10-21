"""/api/daily-measurements — 每日测量, the study's daily questionnaire.

    GET  /api/daily-measurements/history         this account's days, newest first
    GET  /api/daily-measurements/by-date         one day, or null
    GET  /api/daily-measurements/{id}/revisions  every saved version of one day
    POST /api/daily-measurements                 save a day
    PUT  /api/daily-measurements/{id}            correct a day (revision-guarded)

Deliberately separate from /api/assessment (每日记录): its own table, its own
one-per-day rule and its own revisions, so neither form can change the other.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..identity import require_subject_id
from ..models import MEASURE_FIELDS, MEASURE_VERSION, DailyMeasurement, DailyMeasurementRevision
from ..schemas import (
    DailyMeasurementAnswers,
    DailyMeasurementIn,
    DailyMeasurementOut,
    DailyMeasurementPage,
    DailyMeasurementRevisionOut,
    DailyMeasurementUpdate,
)
from .assessment import resolve_record_date

router = APIRouter(prefix="/daily-measurements", tags=["daily-measurements"])


def to_out(row: DailyMeasurement) -> DailyMeasurementOut:
    return DailyMeasurementOut(
        id=row.id, local_date=row.recorded_on, timezone=row.timezone,
        questionnaire_version=row.questionnaire_version, revision_no=row.revision_no,
        submitted_at=row.submitted_at, updated_at=row.updated_at,
        answers=DailyMeasurementAnswers.model_validate({name: getattr(row, name) for name in MEASURE_FIELDS}),
    )


async def _for_day(db: AsyncSession, subject_id: str, on: date) -> DailyMeasurement | None:
    return await db.scalar(select(DailyMeasurement).where(
        DailyMeasurement.subject_id == subject_id, DailyMeasurement.recorded_on == on))


async def _save_revision(db: AsyncSession, row: DailyMeasurement) -> None:
    db.add(DailyMeasurementRevision(measurement_id=row.id, revision_no=row.revision_no,
                                    snapshot=to_out(row).model_dump(mode="json")))


@router.get("/history", response_model=DailyMeasurementPage)
async def measurement_history(
    response: Response,
    limit: int = Query(default=10, ge=1, le=30),
    offset: int = Query(default=0, ge=0),
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> DailyMeasurementPage:
    response.headers["Cache-Control"] = "private, no-store"
    rows = list((await db.scalars(
        select(DailyMeasurement).where(DailyMeasurement.subject_id == subject_id)
        .order_by(DailyMeasurement.recorded_on.desc(), DailyMeasurement.id.desc())
        .offset(offset).limit(limit + 1))).all())
    return DailyMeasurementPage(items=[to_out(row) for row in rows[:limit]], has_more=len(rows) > limit)


@router.get("/by-date", response_model=DailyMeasurementOut | None)
async def measurement_by_date(local_date: date, response: Response,
                              subject_id: str = Depends(require_subject_id), db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    row = await _for_day(db, subject_id, local_date)
    return to_out(row) if row else None


@router.get("/{measurement_id}/revisions", response_model=list[DailyMeasurementRevisionOut])
async def measurement_revisions(measurement_id: int, response: Response,
                                subject_id: str = Depends(require_subject_id), db: AsyncSession = Depends(get_db)):
    owner = await db.scalar(select(DailyMeasurement.id).where(
        DailyMeasurement.id == measurement_id, DailyMeasurement.subject_id == subject_id))
    if owner is None:
        raise HTTPException(404, "记录不存在。")
    response.headers["Cache-Control"] = "private, no-store"
    rows = (await db.scalars(select(DailyMeasurementRevision).where(
        DailyMeasurementRevision.measurement_id == measurement_id)
        .order_by(DailyMeasurementRevision.revision_no.desc()))).all()
    return [DailyMeasurementRevisionOut(revision_no=row.revision_no, saved_at=row.saved_at,
                                        measurement=row.snapshot) for row in rows]


@router.post("", response_model=DailyMeasurementOut, status_code=status.HTTP_201_CREATED)
async def submit_measurement(payload: DailyMeasurementIn, subject_id: str = Depends(require_subject_id),
                             db: AsyncSession = Depends(get_db)) -> DailyMeasurementOut:
    on, tz_name = resolve_record_date(payload.local_date, payload.timezone)
    conflict = HTTPException(409, f"{on} 已有每日测量，请打开该日期修改；不会覆盖原记录。")
    if await _for_day(db, subject_id, on) is not None:
        raise conflict
    row = DailyMeasurement(subject_id=subject_id, recorded_on=on, timezone=tz_name,
                           questionnaire_version=MEASURE_VERSION, revision_no=1, **payload.answers.model_dump())
    db.add(row)
    try:
        await db.flush()
        await db.refresh(row)
        await _save_revision(db, row)
        await db.commit()
    except IntegrityError:
        # Two submits raced; the unique constraint is the real one-per-day rule.
        await db.rollback()
        raise conflict from None
    await db.refresh(row)
    return to_out(row)


@router.put("/{measurement_id}", response_model=DailyMeasurementOut)
async def update_measurement(measurement_id: int, payload: DailyMeasurementUpdate,
                             subject_id: str = Depends(require_subject_id),
                             db: AsyncSession = Depends(get_db)) -> DailyMeasurementOut:
    row = await db.scalar(select(DailyMeasurement).where(
        DailyMeasurement.id == measurement_id, DailyMeasurement.subject_id == subject_id))
    if row is None:
        raise HTTPException(404, "记录不存在。")
    if row.revision_no != payload.expected_revision:
        raise HTTPException(409, "这份每日测量已在别处修改，请重新打开后再编辑。")
    on, _ = resolve_record_date(payload.local_date, row.timezone)
    other = await _for_day(db, subject_id, on)
    if other is not None and other.id != row.id:
        raise HTTPException(409, f"{on} 已有每日测量，请编辑该日期的记录；不会覆盖原记录。")
    try:
        # The revision guard in WHERE makes two concurrent edits fail cleanly.
        changed = await db.execute(update(DailyMeasurement).where(
            DailyMeasurement.id == row.id, DailyMeasurement.revision_no == payload.expected_revision).values(
                **payload.answers.model_dump(), recorded_on=on, revision_no=payload.expected_revision + 1))
        if changed.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, "记录已更新，请重新打开后再修改。")
        await db.refresh(row)
        await _save_revision(db, row)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "该日期已有每日测量或记录已更新，请重新打开后再修改。") from None
    await db.refresh(row)
    return to_out(row)
