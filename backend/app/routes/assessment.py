"""/api/assessment — the Daily Behavioral Activation Assessment.

    GET  /api/assessment/status   has this subject done today yet?
    GET  /api/assessment/history  read completed records, newest first
    POST /api/assessment          save a completed assessment
    POST /api/assessment/skip     record that they were asked and declined

Every endpoint identifies the caller by their bearer token. The resolved
`subject_id` is the account's profile UUID, so both writes and history reads
remain scoped to the authenticated person on every device.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..identity import require_subject_id
from ..models import STATUS_COMPLETED, STATUS_SKIPPED, ActivityLog, AssessmentEntry, AssessmentRevision
from ..schemas import (
    AssessmentOut,
    AssessmentHistoryPage,
    AssessmentSkip,
    AssessmentStatus,
    AssessmentSubmission,
    AssessmentUpdate,
    DailySummaryIn,
    ActivityLogOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assessment", tags=["assessment"])

# Status/skip requests retain their current-day clock-skew tolerance.
# Completed records use resolve_record_date, which also allows backfilling.
_MAX_DATE_DRIFT = timedelta(days=1)


def resolve_timezone(name: str | None) -> tuple[tzinfo, str]:
    """Turn a client-supplied IANA name into a zone, falling back to UTC.

    An unknown zone degrades to UTC rather than 400-ing: the assessment is
    worth more than the precision of its date boundary, and a browser on an
    obscure or stale tz database should still be able to submit.

    The fallback is `datetime.timezone.utc`, deliberately not `ZoneInfo("UTC")`
    — Windows ships no system tz database, so every ZoneInfo lookup there
    depends on the `tzdata` package and the fallback path must not be able to
    raise the very error it exists to absorb.
    """
    if not name:
        return timezone.utc, "UTC"
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown timezone %r from client; falling back to UTC", name)
        return timezone.utc, "UTC"


def resolve_local_date(claimed: date | None, tz_name: str | None) -> tuple[date, str]:
    """Resolve the current-day status/skip request with clock-skew tolerance."""
    tz, resolved_name = resolve_timezone(tz_name)
    server_view = datetime.now(timezone.utc).astimezone(tz).date()

    if claimed is None:
        return server_view, resolved_name

    if abs(claimed - server_view) > _MAX_DATE_DRIFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"local_date {claimed} is too far from the current date in "
                f"{resolved_name} ({server_view})"
            ),
        )
    return claimed, resolved_name


async def _entry_for(
    db: AsyncSession, subject_id: str, on: date
) -> AssessmentEntry | None:
    result = await db.execute(
        select(AssessmentEntry).where(
            AssessmentEntry.subject_id == subject_id,
            AssessmentEntry.recorded_on == on,
        )
    )
    return result.scalar_one_or_none()


def _to_out(entry: AssessmentEntry) -> AssessmentOut:
    return AssessmentOut(
        id=entry.id,
        revision_no=entry.revision_no,
        local_date=entry.recorded_on,
        timezone=entry.timezone,
        status=entry.status,  # type: ignore[arg-type]
        scale_version=entry.scale_version,
        completion_not_applicable=entry.scale_version == 2 and entry.status == STATUS_COMPLETED and entry.completion_rate is None,
        completion_rate=entry.completion_rate,
        activity_level=entry.activity_level,
        social_connection=entry.social_connection,
        approach_vs_avoidance=entry.approach_vs_avoidance,
        overall_mood=entry.overall_mood,
        reflection_note=entry.reflection_note,
        activities=[
            ActivityLogOut(
                position=a.position,
                time_slot=a.time_slot,
                activity=a.activity,
                emotion=a.emotion,
                achievement=a.achievement,
                connection=a.connection,
                enjoyment=a.enjoyment,
                importance=a.importance,
                note=a.note,
            )
            for a in entry.activities
        ],
    )


def resolve_record_date(claimed: date | None, tz_name: str | None) -> tuple[date, str]:
    today, zone = resolve_local_date(None, tz_name)
    on = claimed or today
    if on > today:
        raise HTTPException(422, "不能记录尚未发生的未来日期。")
    return on, zone


async def _save_revision(db: AsyncSession, entry: AssessmentEntry):
    exists = await db.scalar(select(AssessmentRevision.id).where(
        AssessmentRevision.entry_id == entry.id,
        AssessmentRevision.revision_no == entry.revision_no))
    if exists is None:
        db.add(AssessmentRevision(entry_id=entry.id, revision_no=entry.revision_no,
                                 snapshot=_to_out(entry).model_dump(mode="json")))


async def revision_history(db: AsyncSession, entry_id: int):
    rows = (await db.scalars(select(AssessmentRevision).where(
        AssessmentRevision.entry_id == entry_id).order_by(AssessmentRevision.revision_no.desc()))).all()
    return [{"revision_no": row.revision_no, "saved_at": row.saved_at,
             "record": row.snapshot} for row in rows]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status", response_model=AssessmentStatus)
async def assessment_status(
    tz: str | None = None,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> AssessmentStatus:
    """Whether this subject still needs to be prompted for today."""
    on, tz_name = resolve_local_date(None, tz)
    entry = await _entry_for(db, subject_id, on)

    completed = entry is not None and entry.status == STATUS_COMPLETED
    skipped = entry is not None and entry.status == STATUS_SKIPPED

    return AssessmentStatus(
        local_date=on,
        timezone=tz_name,
        has_completed_today=completed,
        has_skipped_today=skipped,
        should_prompt=not (completed or skipped),
    )


@router.get("/history", response_model=AssessmentHistoryPage)
async def assessment_history(
    limit: int = Query(default=10, ge=1, le=30),
    offset: int = Query(default=0, ge=0),
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> AssessmentHistoryPage:
    """Return this account's completed daily records, newest first.

    Skipped days contain no user-authored record and are intentionally omitted.
    Fetching one extra row tells the client whether to offer "load more"
    without running a second COUNT query on every page.
    """
    result = await db.execute(
        select(AssessmentEntry)
        .where(
            AssessmentEntry.subject_id == subject_id,
            AssessmentEntry.status == STATUS_COMPLETED,
        )
        .options(selectinload(AssessmentEntry.activities))
        .order_by(AssessmentEntry.recorded_on.desc(), AssessmentEntry.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    entries = list(result.scalars().unique().all())
    has_more = len(entries) > limit
    page = entries[:limit]
    return AssessmentHistoryPage(
        items=[_to_out(entry) for entry in page],
        has_more=has_more,
        next_offset=offset + len(page) if has_more else None,
    )


@router.post(
    "", response_model=AssessmentOut, status_code=status.HTTP_201_CREATED
)
async def submit_assessment(
    payload: AssessmentSubmission,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> AssessmentOut:
    """Save a completed assessment for one local day."""
    on, tz_name = resolve_record_date(payload.local_date, payload.timezone)
    entry = await _entry_for(db, subject_id, on)

    if entry is not None and entry.status == STATUS_COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{on} 已有记录，请在历史记录中修改；不会覆盖原记录。",
        )

    if entry is None:
        entry = AssessmentEntry(subject_id=subject_id, recorded_on=on, activities=[])
        db.add(entry)
    else:
        await _save_revision(db, entry)
        entry.revision_no += 1
        # Upgrading an earlier skip into a real submission. Clearing the list
        # relies on delete-orphan so a re-submit can't accumulate stale cards.
        entry.activities.clear()

    entry.timezone = tz_name
    entry.status = STATUS_COMPLETED
    entry.scale_version = 2
    entry.completion_rate = payload.summary.completion_rate
    entry.activity_level = payload.summary.activity_level
    entry.social_connection = None
    entry.approach_vs_avoidance = None
    entry.overall_mood = payload.summary.overall_mood
    entry.reflection_note = payload.summary.reflection_note

    for position, item in enumerate(payload.activities):
        entry.activities.append(
            ActivityLog(
                position=position,
                time_slot=item.time_slot,
                activity=item.activity,
                emotion=item.emotion,
                achievement=item.achievement,
                connection=item.connection,
                enjoyment=item.enjoyment,
                importance=item.importance,
                note=item.note,
            )
        )

    try:
        await db.flush()
        await _save_revision(db, entry)
        await db.commit()
    except IntegrityError:
        # Two submits raced. The unique constraint is what actually enforces
        # one-per-day; the check above only produces the nicer error first.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{on} 已有记录，请在历史记录中修改；不会覆盖原记录。",
        ) from None

    await db.refresh(entry)
    return _to_out(entry)


@router.post("/skip", response_model=AssessmentOut, status_code=status.HTTP_201_CREATED)
async def skip_assessment(
    payload: AssessmentSkip,
    subject_id: str = Depends(require_subject_id),
    db: AsyncSession = Depends(get_db),
) -> AssessmentOut:
    """Record that today's assessment was offered and declined.

    Persisted rather than held in the browser so the prompt does not return on
    the next reload. Someone who skipped because they are having a bad day
    should not have to decline the same modal five more times.
    """
    on, tz_name = resolve_local_date(payload.local_date, payload.timezone)
    entry = await _entry_for(db, subject_id, on)

    if entry is not None:
        # Already completed: a skip must not erase real data. Already skipped:
        # nothing to do. Either way the stored row is the answer.
        return _to_out(entry)

    entry = AssessmentEntry(
        subject_id=subject_id,
        recorded_on=on,
        timezone=tz_name,
        status=STATUS_SKIPPED,
    )
    db.add(entry)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await _entry_for(db, subject_id, on)
        if existing is None:  # pragma: no cover - constraint fired for another reason
            raise
        return _to_out(existing)

    await db.refresh(entry)
    return _to_out(entry)


@router.get("/by-date", response_model=AssessmentOut | None)
async def assessment_by_date(local_date: date, response: Response, subject_id: str = Depends(require_subject_id),
                             db: AsyncSession = Depends(get_db)):
    response.headers['Cache-Control'] = 'private, no-store'
    entry = await _entry_for(db, subject_id, local_date)
    return _to_out(entry) if entry and entry.status == STATUS_COMPLETED else None


@router.get("/{entry_id}/revisions")
async def assessment_revisions(entry_id: int, response: Response, subject_id: str = Depends(require_subject_id),
                               db: AsyncSession = Depends(get_db)):
    entry = await db.scalar(select(AssessmentEntry).where(
        AssessmentEntry.id == entry_id, AssessmentEntry.subject_id == subject_id))
    if entry is None:
        raise HTTPException(404, "记录不存在。")
    response.headers['Cache-Control'] = 'private, no-store'
    return await revision_history(db, entry_id)


@router.put("/{entry_id}", response_model=AssessmentOut)
async def update_assessment(entry_id: int, payload: AssessmentUpdate,
                            subject_id: str = Depends(require_subject_id),
                            db: AsyncSession = Depends(get_db)):
    entry = await db.scalar(select(AssessmentEntry).where(
        AssessmentEntry.id == entry_id, AssessmentEntry.subject_id == subject_id,
        AssessmentEntry.status == STATUS_COMPLETED).with_for_update())
    if entry is None:
        raise HTTPException(404, "记录不存在。")
    if entry.revision_no != payload.expected_revision:
        raise HTTPException(409, "这份记录已在别处修改，请重新打开最新记录后再编辑。")
    on, _ = resolve_record_date(payload.local_date, entry.timezone)
    other = await _entry_for(db, subject_id, on)
    if other is not None and other.id != entry.id:
        raise HTTPException(409, f"{on} 已有记录，请编辑该日期的记录；不会覆盖原记录。")
    values = payload.summary.model_dump(exclude={"completion_not_applicable"})
    if entry.scale_version == 2:
        try:
            DailySummaryIn.model_validate(payload.summary.model_dump())
        except ValueError:
            raise HTTPException(422, "请填写有效的 0–5 分总体评分；不适用时请明确选择。") from None
        # Fields absent from today's form remain untouched, not silently erased.
        for name in ('social_connection', 'approach_vs_avoidance'):
            values.pop(name)
    try:
        await _save_revision(db, entry)  # Lazy baseline for pre-existing records.
        changed = await db.execute(update(AssessmentEntry).where(
            AssessmentEntry.id == entry.id, AssessmentEntry.subject_id == subject_id,
            AssessmentEntry.revision_no == payload.expected_revision).values(
                **values, recorded_on=on, revision_no=payload.expected_revision + 1))
        if changed.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, "记录已更新，请重新打开后再修改。")
        entry.activities.clear()
        for position, item in enumerate(payload.activities):
            entry.activities.append(ActivityLog(position=position, **item.model_dump()))
        await db.flush()
        await _save_revision(db, entry)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "该日期已有记录或记录已更新，请重新打开后再修改。") from None
    await db.refresh(entry)
    return _to_out(entry)
