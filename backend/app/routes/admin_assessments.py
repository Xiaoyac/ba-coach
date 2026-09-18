"""Read-only administrator access to daily records; explicit scales and missingness."""
import csv
import io
import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..identity import CallerIdentity, require_admin
from ..models import AccountHandle, AssessmentEntry, STATUS_COMPLETED, UserAccount
from .assessment import _to_out

router = APIRouter(prefix='/admin/assessments', tags=['admin-assessments'], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)
EXPORT_LIMIT = 1000


def filters(query: str = Query('', max_length=64), start: date | None = None,
            end: date | None = None, scale_version: int | None = Query(None, ge=1, le=2)):
    if start and end and start > end:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    clauses = [AssessmentEntry.status == STATUS_COMPLETED]
    if query.strip():
        needle = query.strip()
        clauses.append(or_(UserAccount.username.contains(needle, autoescape=True),
            (AccountHandle.base_username + '#' + AccountHandle.tag).contains(needle, autoescape=True),
            AssessmentEntry.subject_id.contains(needle, autoescape=True)))
    if start:
        clauses.append(AssessmentEntry.recorded_on >= start)
    if end:
        clauses.append(AssessmentEntry.recorded_on <= end)
    if scale_version:
        clauses.append(AssessmentEntry.scale_version == scale_version)
    return clauses


def joined(statement):
    return statement.select_from(AssessmentEntry).outerjoin(UserAccount, UserAccount.profile_uuid == AssessmentEntry.subject_id).outerjoin(AccountHandle, AccountHandle.account_id == UserAccount.id)


def records(clauses):
    return joined(select(AssessmentEntry, UserAccount.username, AccountHandle.base_username, AccountHandle.tag)).where(*clauses).options(selectinload(AssessmentEntry.activities)).order_by(AssessmentEntry.recorded_on.desc(), AssessmentEntry.id.desc())


def item(row):
    entry, username, nickname, tag = row
    return {'subject_id': entry.subject_id, 'username': username,
            'display_id': f'{nickname}#{tag}' if nickname and tag else None,
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
    common = ['record_id', 'subject_id', 'username', 'display_id', 'local_date', 'timezone', 'scale_version', 'summary_scale_max',
              'completion_rate', 'completion_not_applicable', 'activity_level', 'overall_mood', 'social_connection_legacy', 'approach_vs_avoidance_legacy', 'reflection_note']
    extra = ['activity_position', 'time_slot', 'activity', 'emotion', 'achievement', 'connection', 'enjoyment', 'importance', 'activity_note', 'activity_scale_max']
    writer.writerow(common + (extra if kind == 'activities' else ['activity_count']))
    for row in rows:
        data = item(row)
        record = data['record']
        values = [record.id, data['subject_id'], data['username'], data['display_id'], record.local_date, record.timezone,
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
