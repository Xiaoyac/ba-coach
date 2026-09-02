"""Authenticated product feedback with administrator-only diagnostics."""

from __future__ import annotations

import base64
import binascii
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..identity import CallerIdentity, require_admin, require_caller
from ..models import AccountHandle, IssueReport, UserAccount, _utcnow
from ..schemas import (
    AdminIssueReportItem,
    AdminIssueReportList,
    AdminIssueReportStatusUpdate,
    IssueReportCreate,
    IssueReportCreated,
)

router = APIRouter(tags=["issue-reports"])

MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
MAX_REPORTS_PER_HOUR = 10
ALLOWED_SCREENSHOTS = {
    "data:image/jpeg;base64": ("image/jpeg", b"\xff\xd8\xff"),
    "data:image/png;base64": ("image/png", b"\x89PNG\r\n\x1a\n"),
    "data:image/webp;base64": ("image/webp", b"RIFF"),
}


def _decode_screenshot(data_url: str | None) -> tuple[bytes | None, str | None]:
    if not data_url:
        return None, None

    header, separator, encoded = data_url.partition(",")
    if not separator or header not in ALLOWED_SCREENSHOTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="截图格式不受支持，请使用 JPEG、PNG 或 WebP",
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="截图数据无效",
        ) from None

    if not raw or len(raw) > MAX_SCREENSHOT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="截图超过 2 MB，请移除截图后再提交",
        )

    mime, signature = ALLOWED_SCREENSHOTS[header]
    valid_signature = raw.startswith(signature)
    if mime == "image/webp":
        valid_signature = valid_signature and len(raw) >= 12 and raw[8:12] == b"WEBP"
    if not valid_signature:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="截图内容与图片格式不一致",
        )
    return raw, mime


def _admin_item(report: IssueReport, account: UserAccount, handle: AccountHandle | None) -> AdminIssueReportItem:
    return AdminIssueReportItem(
        id=report.id,
        username=account.username,
        display_name=handle.full_username if handle else None,
        description=report.description,
        status=report.status,
        has_screenshot=report.screenshot is not None,
        screenshot_mime=report.screenshot_mime,
        page_url=report.page_url,
        session_id=report.session_id,
        last_error=report.last_error,
        user_agent=report.user_agent,
        viewport_width=report.viewport_width,
        viewport_height=report.viewport_height,
        client_online=report.client_online,
        created_at=report.created_at,
        resolved_at=report.resolved_at,
    )


@router.post(
    "/issue-reports",
    response_model=IssueReportCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_issue_report(
    payload: IssueReportCreate,
    caller: CallerIdentity = Depends(require_caller),
    db: AsyncSession = Depends(get_db),
) -> IssueReportCreated:
    recent_count = (
        await db.execute(
            select(func.count(IssueReport.id)).where(
                IssueReport.account_id == caller.account.id,
                IssueReport.created_at >= _utcnow() - timedelta(hours=1),
            )
        )
    ).scalar_one()
    if recent_count >= MAX_REPORTS_PER_HOUR:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="提交得有点频繁，请稍后再试",
        )

    screenshot, screenshot_mime = _decode_screenshot(payload.screenshot_data_url)
    report = IssueReport(
        account_id=caller.account.id,
        subject_id=caller.subject_id,
        description=payload.description,
        screenshot=screenshot,
        screenshot_mime=screenshot_mime,
        page_url=payload.page_url,
        session_id=payload.session_id,
        last_error=payload.last_error,
        user_agent=payload.user_agent,
        viewport_width=payload.viewport_width,
        viewport_height=payload.viewport_height,
        client_online=payload.client_online,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return IssueReportCreated(id=report.id, created_at=report.created_at)


@router.get("/admin/issue-reports", response_model=AdminIssueReportList)
async def list_issue_reports(
    report_status: str | None = Query(default=None, alias="status", pattern="^(open|resolved)$"),
    limit: int = Query(default=50, ge=1, le=100),
    _caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminIssueReportList:
    statement = (
        select(IssueReport, UserAccount, AccountHandle)
        .join(UserAccount, UserAccount.id == IssueReport.account_id)
        .outerjoin(AccountHandle, AccountHandle.account_id == UserAccount.id)
    )
    if report_status:
        statement = statement.where(IssueReport.status == report_status)
    rows = (
        await db.execute(statement.order_by(IssueReport.created_at.desc()).limit(limit))
    ).all()
    return AdminIssueReportList(
        reports=[_admin_item(report, account, handle) for report, account, handle in rows]
    )


@router.get("/admin/issue-reports/{report_id}/screenshot")
async def get_issue_report_screenshot(
    report_id: int,
    _caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    report = await db.get(IssueReport, report_id)
    if report is None or report.screenshot is None or report.screenshot_mime is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到截图")
    return Response(
        content=report.screenshot,
        media_type=report.screenshot_mime,
        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"},
    )


@router.patch(
    "/admin/issue-reports/{report_id}/status",
    response_model=AdminIssueReportItem,
)
async def update_issue_report_status(
    report_id: int,
    payload: AdminIssueReportStatusUpdate,
    _caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminIssueReportItem:
    row = (
        await db.execute(
            select(IssueReport, UserAccount, AccountHandle)
            .join(UserAccount, UserAccount.id == IssueReport.account_id)
            .outerjoin(AccountHandle, AccountHandle.account_id == UserAccount.id)
            .where(IssueReport.id == report_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到该反馈")

    report, account, handle = row
    report.status = payload.status
    report.resolved_at = _utcnow() if payload.status == "resolved" else None
    await db.commit()
    await db.refresh(report)
    return _admin_item(report, account, handle)
