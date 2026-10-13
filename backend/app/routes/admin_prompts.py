"""Admin-only prompt editing, immutable history, comparison and restoration."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..account_identity import audit_identity
from ..identity import CallerIdentity, require_admin
from ..prompt_store import PROMPT_DEFINITIONS, PROMPT_DEFINITION_BY_KEY
from ..prompt_history import snapshot, change_prompt, compare_text
from ..prompt_history_models import PromptRevision
from ..schemas import AdminPromptBundle, AdminPromptItem, AdminPromptUpdate, AdminPromptRestore, PromptKey

router = APIRouter(prefix='/admin/prompts', tags=['admin-prompts'])


def item(row):
    definition = PROMPT_DEFINITION_BY_KEY[row.prompt_key]
    return AdminPromptItem(key=row.prompt_key, label=definition.label, description=definition.description,
        content=row.content, is_overridden=row.is_overridden, updated_by=row.actor,
        updated_at=row.created_at, revision=row.revision)


def version(row, *, include_content=False):
    result = dict(revision=row.revision, actor=row.actor, action=row.action, created_at=row.created_at,
                  is_overridden=row.is_overridden, restored_from=row.restored_from,
                  origin='system' if row.actor.startswith(('system:', 'deployment:')) else 'admin')
    if include_content: result['content'] = row.content
    return result


async def get_version(db, key, revision):
    row = (await db.execute(select(PromptRevision).where(PromptRevision.prompt_key == key,
                                                        PromptRevision.revision == revision))).scalar_one_or_none()
    if row is None: raise HTTPException(404, '这个提示词版本不存在')
    return row


@router.get('', response_model=AdminPromptBundle)
async def list_admin_prompts(_caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return AdminPromptBundle(prompts=[item(await snapshot(db, d.key)) for d in PROMPT_DEFINITIONS])


@router.put('/{prompt_key}', response_model=AdminPromptItem)
async def update_admin_prompt(prompt_key: PromptKey, payload: AdminPromptUpdate,
                              caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    content = payload.content.strip()
    if not content: raise HTTPException(400, '提示词不能只包含空白字符')
    return item(await change_prompt(db,prompt_key,actor=await audit_identity(db,caller.account),
                                   base_revision=payload.base_revision,content=content))


@router.delete('/{prompt_key}', response_model=AdminPromptItem)
async def reset_admin_prompt(prompt_key: PromptKey, base_revision: int = Query(..., ge=1),
                             caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return item(await change_prompt(db,prompt_key,actor=await audit_identity(db,caller.account),base_revision=base_revision))


@router.get('/{prompt_key}/history')
async def list_history(prompt_key: PromptKey, before: int | None = Query(None,ge=1),
                       limit: int = Query(20,ge=1,le=100), _caller: CallerIdentity = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    query = select(PromptRevision).where(PromptRevision.prompt_key == prompt_key)
    if before is not None: query = query.where(PromptRevision.revision < before)
    rows = list((await db.execute(query.order_by(PromptRevision.revision.desc()).limit(limit+1))).scalars())
    return {'items':[version(r) for r in rows[:limit]], 'next_before':rows[limit-1].revision if len(rows)>limit else None}


@router.get('/{prompt_key}/history/{revision}')
async def read_version(prompt_key: PromptKey, revision: int, _caller: CallerIdentity = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    return version(await get_version(db,prompt_key,revision),include_content=True)


@router.get('/{prompt_key}/compare')
async def compare_versions(prompt_key: PromptKey, from_revision: int = Query(...,ge=1),
                           to_revision: int = Query(...,ge=1), _caller: CallerIdentity = Depends(require_admin),
                           db: AsyncSession = Depends(get_db)):
    old,new = await get_version(db,prompt_key,from_revision),await get_version(db,prompt_key,to_revision)
    return {'before':version(old),'after':version(new),'changes':compare_text(old.content,new.content)}


@router.post('/{prompt_key}/history/{revision}/restore', response_model=AdminPromptItem)
async def restore_version(prompt_key: PromptKey, revision: int, payload: AdminPromptRestore,
                          caller: CallerIdentity = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    old = await get_version(db,prompt_key,revision)
    return item(await change_prompt(db,prompt_key,actor=await audit_identity(db,caller.account),
        base_revision=payload.base_revision,content=old.content,restored_from=revision))
