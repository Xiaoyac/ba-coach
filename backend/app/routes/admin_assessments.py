"""Read-only administrator access to daily records; explicit scales and missingness."""
import csv
import io
import logging
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..identity import CallerIdentity, require_admin
from ..models import AccountSettings, ActivityLog, AssessmentEntry, STATUS_COMPLETED, UserAccount
from ..models_business import UserProfile
from .assessment import _to_out, revision_history

router = APIRouter(prefix='/admin/assessments', tags=['admin-assessments'], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)
EXPORT_LIMIT = 1000
ADMIN_RECORD_TIME_ZONE = 'Asia/Shanghai'


def _today() -> date:
    """Use the server's Beijing calendar, independently of the browser clock."""
    return datetime.now(ZoneInfo(ADMIN_RECORD_TIME_ZONE)).date()


def _ordinary_users(statement):
    """Accounts without settings have the same ordinary role as auth defaults."""
    return (statement.select_from(UserAccount)
            .outerjoin(AccountSettings, AccountSettings.account_id == UserAccount.id)
            .where(func.coalesce(AccountSettings.role, 'user') == 'user'))


async def _require_record_user(db: AsyncSession, subject_id: str) -> None:
    account_id = await db.scalar(_ordinary_users(select(UserAccount.id)).where(
        UserAccount.profile_uuid == subject_id))
    if account_id is None:
        raise HTTPException(404, '用户不存在或不在普通用户记录列表中。',
                            headers={'Cache-Control': 'no-store'})


def filters(query: str = Query('', max_length=64), start: date | None = None,
            end: date | None = None, scale_version: int | None = Query(None, ge=1, le=2)):
    if start and end and start > end:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    clauses = [AssessmentEntry.status == STATUS_COMPLETED]
    if query.strip():
        needle = query.strip()
        clauses.append(or_(UserAccount.username.contains(needle, autoescape=True),
            UserProfile.nickname.contains(needle, autoescape=True),
            AssessmentEntry.subject_id.contains(needle, autoescape=True)))
    if start:
        clauses.append(AssessmentEntry.recorded_on >= start)
    if end:
        clauses.append(AssessmentEntry.recorded_on <= end)
    if scale_version:
        clauses.append(AssessmentEntry.scale_version == scale_version)
    return clauses


def joined(statement):
    return statement.select_from(AssessmentEntry).outerjoin(UserAccount, UserAccount.profile_uuid == AssessmentEntry.subject_id).outerjoin(UserProfile, UserProfile.uuid == UserAccount.profile_uuid)


def records(clauses):
    return joined(select(AssessmentEntry, UserAccount.username, UserProfile.nickname)).where(*clauses).options(selectinload(AssessmentEntry.activities)).order_by(AssessmentEntry.recorded_on.desc(), AssessmentEntry.id.desc())


def item(row):
    entry, username, nickname = row
    return {'subject_id': entry.subject_id, 'username': username,
            'nickname': nickname,
            'record': _to_out(entry)}


@router.get('')
async def list_records(response: Response, clauses=Depends(filters), limit: int = Query(20, ge=1, le=50),
                       offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    stats = (await db.execute(joined(select(func.count(AssessmentEntry.id), func.count(func.distinct(AssessmentEntry.subject_id)))).where(*clauses))).one()
    versions = (await db.execute(joined(select(AssessmentEntry.scale_version, func.count(AssessmentEntry.id))).where(*clauses).group_by(AssessmentEntry.scale_version))).all()
    rows = (await db.execute(records(clauses).offset(offset).limit(limit))).all()
    return {'items': [item(row) for row in rows], 'total': stats[0], 'users': stats[1],
            'versions': {str(version): count for version, count in versions},
            'has_more': offset + len(rows) < stats[0]}


@router.get('/users')
async def list_record_users(response: Response, query: str = Query('', max_length=64),
                            limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0),
                            db: AsyncSession = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    today = _today()
    # Aggregate records before joining the account directory: zero-record users
    # remain visible, and activity rows cannot multiply the record counts.
    totals = (select(
        AssessmentEntry.subject_id.label('subject_id'),
        func.count(AssessmentEntry.id).label('record_count'),
        func.count(func.distinct(AssessmentEntry.recorded_on)).label('date_count'),
        func.max(AssessmentEntry.recorded_on).label('last_record_date'),
        func.max(case((AssessmentEntry.recorded_on == today, 1), else_=0)).label('today_completed'),
    ).where(AssessmentEntry.status == STATUS_COMPLETED)
        .group_by(AssessmentEntry.subject_id).subquery())
    directory = (_ordinary_users(select(
        UserAccount.id, UserAccount.profile_uuid, UserAccount.username,
        UserProfile.nickname,
    )).outerjoin(UserProfile, UserProfile.uuid == UserAccount.profile_uuid))
    if query.strip():
        needle = query.strip()
        directory = directory.where(or_(
            UserAccount.username.contains(needle, autoescape=True),
            UserProfile.nickname.contains(needle, autoescape=True),
            UserAccount.profile_uuid.contains(needle, autoescape=True),
        ))
    total = await db.scalar(select(func.count()).select_from(directory.subquery()))
    statement = (directory.add_columns(totals.c.record_count, totals.c.date_count,
        totals.c.last_record_date, totals.c.today_completed)
        .outerjoin(totals, totals.c.subject_id == UserAccount.profile_uuid)
        .order_by(UserAccount.id.desc()).offset(offset).limit(limit))
    rows = (await db.execute(statement)).all()
    items = [{
        'subject_id': row.profile_uuid, 'username': row.username,
        'nickname': row.nickname,
        'today_completed': bool(row.today_completed),
        'record_count': row.record_count or 0, 'date_count': row.date_count or 0,
        'last_record_date': row.last_record_date,
    } for row in rows]
    return {'items': items, 'total': total, 'has_more': offset + len(items) < total,
            'today': today, 'time_zone': ADMIN_RECORD_TIME_ZONE}


@router.get('/users/{subject_id}/dates')
async def list_record_dates(subject_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    await _require_record_user(db, subject_id)
    rows = (await db.execute(select(
        AssessmentEntry.recorded_on.label('local_date'),
        func.count(func.distinct(AssessmentEntry.id)).label('record_count'),
        func.count(ActivityLog.id).label('activity_count'),
    ).select_from(AssessmentEntry)
        .outerjoin(ActivityLog, ActivityLog.entry_id == AssessmentEntry.id)
        .where(AssessmentEntry.subject_id == subject_id, AssessmentEntry.status == STATUS_COMPLETED)
        .group_by(AssessmentEntry.recorded_on)
        .order_by(AssessmentEntry.recorded_on.desc()))).all()
    # No pagination here: expanding a person shows their entire date directory.
    return {'subject_id': subject_id, 'items': [dict(row._mapping) for row in rows],
            'total_dates': len(rows)}


@router.get('/users/{subject_id}/days/{local_date}')
async def records_for_day(subject_id: str, local_date: date, response: Response,
                          db: AsyncSession = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    await _require_record_user(db, subject_id)
    rows = (await db.execute(records([
        AssessmentEntry.subject_id == subject_id,
        AssessmentEntry.recorded_on == local_date,
        AssessmentEntry.status == STATUS_COMPLETED,
    ]))).all()
    return {'subject_id': subject_id, 'local_date': local_date,
            'items': [item(row) for row in rows], 'total': len(rows)}


def safe_cell(value):
    """Prevent formula execution when untrusted free text is opened in Excel."""
    if value is None:
        return ''
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
        return "'" + value
    return value


@router.get('/export.csv')
async def export_records(kind: Literal['daily', 'activities'] = 'daily', clauses=Depends(filters),
                         caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    total = await db.scalar(joined(select(func.count(AssessmentEntry.id))).where(*clauses))
    if total > EXPORT_LIMIT:
        raise HTTPException(422, f'单次最多导出 {EXPORT_LIMIT} 份每日记录，请缩小日期或用户范围')
    rows = (await db.execute(records(clauses).limit(EXPORT_LIMIT + 1))).all()
    if len(rows) > EXPORT_LIMIT:
        raise HTTPException(422, '记录增加超过导出上限，请缩小筛选范围')
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    common = ['record_id', 'subject_id', 'username', 'nickname', 'local_date', 'timezone', 'scale_version', 'summary_scale_max',
              'completion_rate', 'completion_not_applicable', 'activity_level', 'overall_mood', 'social_connection_legacy', 'approach_vs_avoidance_legacy', 'reflection_note']
    extra = ['activity_position', 'time_slot', 'activity', 'emotion', 'achievement', 'connection', 'enjoyment', 'importance', 'activity_note', 'activity_scale_max']
    writer.writerow(common + (extra if kind == 'activities' else ['activity_count']))
    for row in rows:
        data = item(row)
        record = data['record']
        values = [record.id, data['subject_id'], data['username'], data['nickname'], record.local_date, record.timezone,
                  record.scale_version, 5 if record.scale_version == 2 else 10, record.completion_rate,
                  record.completion_not_applicable, record.activity_level, record.overall_mood,
                  record.social_connection, record.approach_vs_avoidance, record.reflection_note]
        if kind == 'daily':
            writer.writerow([safe_cell(v) for v in values + [len(record.activities)]])
        else:
            for activity in record.activities:
                writer.writerow([safe_cell(v) for v in values + [activity.position, activity.time_slot, activity.activity, activity.emotion,
                    activity.achievement, activity.connection, activity.enjoyment, activity.importance, activity.note, 5]])
    logger.info('admin_daily_export actor=%s kind=%s records=%s', caller.account.id, kind, len(rows))
    return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8', headers={
        'Content-Disposition': f'attachment; filename="daily-records-{kind}.csv"', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/{entry_id}/revisions')
async def assessment_revision_history(entry_id: int, response: Response, db: AsyncSession = Depends(get_db)):
    if await db.get(AssessmentEntry, entry_id) is None:
        raise HTTPException(404, '记录不存在。')
    response.headers["Cache-Control"] = "private, no-store"
    return await revision_history(db, entry_id)
