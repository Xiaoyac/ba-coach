"""Durable administrator overrides for coaching and routing prompts."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import PromptOverride
from .prompts import GLOBAL_PROMPT, MODULE_PROMPTS
from .router_agent import ROUTER_AGENT_PROMPT, ROUTER_RUNTIME_CONTRACT
from .knowledge_mediator import MEDIATOR_PROMPT


@dataclass(frozen=True)
class PromptDefinition:
    key: str
    label: str
    description: str

    @property
    def default_content(self) -> str:
        if self.key == "global":
            return GLOBAL_PROMPT
        if self.key == "router_agent":
            return ROUTER_AGENT_PROMPT
        if self.key == "knowledge_mediator":
            return MEDIATOR_PROMPT
        return MODULE_PROMPTS[self.key]


PROMPT_DEFINITIONS: tuple[PromptDefinition, ...] = (
    PromptDefinition("global", "全局提示词", "适用于所有模块的角色、原则与输出规范"),
    PromptDefinition("module_1", "MODULE I", "理解困扰与具体情境"),
    PromptDefinition("module_2", "MODULE II", "目标设定与活动计划"),
    PromptDefinition("module_3", "MODULE III", "计划执行前记录提醒"),
    PromptDefinition("module_4", "MODULE IV", "复盘、调整与巩固"),
    PromptDefinition(
        "router_agent",
        "模块跳转 Agent",
        "判断下一模块；服务器会另行追加不可覆盖的跨轮判定与防循环规则",
    ),
    PromptDefinition("knowledge_mediator", "知识使用中介 LLM", "检索后指导各模块使用知识；保存后新回合生效，接口与安全约束由服务器追加"),
)
PROMPT_DEFINITION_BY_KEY = {item.key: item for item in PROMPT_DEFINITIONS}


async def get_overrides(db: AsyncSession) -> dict[str, PromptOverride]:
    rows = (
        await db.execute(
            select(PromptOverride).where(
                PromptOverride.prompt_key.in_(PROMPT_DEFINITION_BY_KEY)
            )
        )
    ).scalars()
    return {row.prompt_key: row for row in rows}


async def effective_prompt_pair(
    db: AsyncSession, module_name: str
) -> tuple[str, str]:
    """Return the effective global and selected-module prompt for one turn."""
    if module_name not in MODULE_PROMPTS:
        raise KeyError(module_name)
    rows = (
        await db.execute(
            select(PromptOverride).where(
                PromptOverride.prompt_key.in_(("global", module_name))
            )
        )
    ).scalars()
    overrides = {row.prompt_key: row.content for row in rows}
    return (
        overrides.get("global", GLOBAL_PROMPT),
        overrides.get(module_name, MODULE_PROMPTS[module_name]),
    )


async def effective_router_prompt(db: AsyncSession) -> str:
    """Return the editable router prompt plus non-overridable state rules."""
    row = (
        await db.execute(
            select(PromptOverride).where(PromptOverride.prompt_key == "router_agent")
        )
    ).scalar_one_or_none()
    editable = row.content if row else ROUTER_AGENT_PROMPT
    return editable + "\n\n" + ROUTER_RUNTIME_CONTRACT


async def effective_mediator_prompt(db: AsyncSession) -> str:
    row = (await db.execute(select(PromptOverride).where(PromptOverride.prompt_key == "knowledge_mediator"))).scalar_one_or_none()
    return row.content if row else MEDIATOR_PROMPT
