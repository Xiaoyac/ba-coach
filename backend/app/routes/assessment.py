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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..identity import require_subject_id
from ..models import STATUS_COMPLETED, STATUS_SKIPPED, ActivityLog, AssessmentEntry
from ..schemas import (
    AssessmentOut,
    AssessmentHistoryPage,
    AssessmentSkip,
    AssessmentStatus,
    AssessmentSubmission,
    ActivityLogOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assessment", tags=["assessment"])

# How far a client-supplied `local_date` may sit from the server's own idea of
# the caller's local date. One day absorbs clock skew and the midnight edge;
# anything further is a client backfilling arbitrary history, which this
# endpoint deliberately does not offer.
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
    """Decide which local date a request is about.

    The server computes the date from the caller's timezone. A client may state
    one explicitly, but only within a day of that — otherwise "today" would be
    whatever the client said it was, and the one-per-day rule would mean
    nothing.
    """
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
    on, tz_name = resolve_local_date(payload.local_date, payload.timezone)
    entry = await _entry_for(db, subject_id, on)

    if entry is not None and entry.status == STATUS_COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An assessment for {on} has already been submitted",
        )

    if entry is None:
        entry = AssessmentEntry(subject_id=subject_id, recorded_on=on)
        db.add(entry)
    else:
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
        await db.commit()
    except IntegrityError:
        # Two submits raced. The unique constraint is what actually enforces
        # one-per-day; the check above only produces the nicer error first.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An assessment for {on} has already been submitted",
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
