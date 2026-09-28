"""Deliberately limited share schemas: transcript and existing detail panels."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .knowledge_references import KnowledgeReferences
from .schemas import MessageTiming


class SharedMessage(BaseModel):
    # Keep this public allowlist independent of private conversation schemas:
    # later additions to those must not silently become public share fields.
    # The id is snapshot-local, never a live message API identifier.
    id: int
    role: Literal["user", "assistant"]
    content: str
    reasoning_content: str | None = None
    model_name: str | None = None
    routing_reasoning_content: str | None = None
    router_model_name: str | None = None
    timing: MessageTiming | None = None
    knowledge_references: KnowledgeReferences | None = None


class ConversationShareSnapshot(BaseModel):
    snapshot_version: Literal[1] = 1
    title: str
    created_at: datetime
    messages: list[SharedMessage] = Field(default_factory=list)


class ConversationShareCreated(BaseModel):
    id: str
    title: str
    created_at: datetime
    message_count: int
    snapshot_version: Literal[1] = 1
    token: str
    path: str
