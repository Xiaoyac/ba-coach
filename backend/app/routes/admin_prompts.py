"""Administrator-only prompt inspection and live override API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..account_identity import audit_identity
from ..identity import CallerIdentity, require_admin
from ..models import PromptOverride, _utcnow
from ..prompt_store import (
    PROMPT_DEFINITION_BY_KEY,
    PROMPT_DEFINITIONS,
    get_overrides,
)
from ..schemas import AdminPromptBundle, AdminPromptItem, AdminPromptUpdate, PromptKey

router = APIRouter(prefix="/admin/prompts", tags=["admin-prompts"])


def _item(definition, override: PromptOverride | None) -> AdminPromptItem:
    return AdminPromptItem(
        key=definition.key,
        label=definition.label,
        description=definition.description,
        content=override.content if override else definition.default_content,
        is_overridden=override is not None,
        updated_by=override.updated_by if override else None,
        updated_at=override.updated_at if override else None,
    )


@router.get("", response_model=AdminPromptBundle)
async def list_admin_prompts(
    _caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminPromptBundle:
    overrides = await get_overrides(db)
    return AdminPromptBundle(
        prompts=[_item(item, overrides.get(item.key)) for item in PROMPT_DEFINITIONS]
    )


@router.put("/{prompt_key}", response_model=AdminPromptItem)
async def update_admin_prompt(
    prompt_key: PromptKey,
    payload: AdminPromptUpdate,
    caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminPromptItem:
    content = payload.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="提示词不能只包含空白字符",
        )

    definition = PROMPT_DEFINITION_BY_KEY[prompt_key]
    editor_name = await audit_identity(db, caller.account)
    overrides = await get_overrides(db)
    override = overrides.get(prompt_key)
    if override is None:
        override = PromptOverride(
            prompt_key=prompt_key,
            content=content,
            updated_by=editor_name,
        )
        db.add(override)
    else:
        override.content = content
        override.updated_by = editor_name
        override.updated_at = _utcnow()
    await db.commit()
    await db.refresh(override)
    return _item(definition, override)


@router.delete("/{prompt_key}", response_model=AdminPromptItem)
async def reset_admin_prompt(
    prompt_key: PromptKey,
    _caller: CallerIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminPromptItem:
    definition = PROMPT_DEFINITION_BY_KEY[prompt_key]
    overrides = await get_overrides(db)
    override = overrides.get(prompt_key)
    if override is not None:
        await db.delete(override)
        await db.commit()
    return _item(definition, None)
