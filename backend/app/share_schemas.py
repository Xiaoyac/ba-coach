"""Explicit transcript and administrator-debug snapshot contracts."""
from datetime import datetime
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from .assistant_content import unwrap_assistant_message
from .reasoning import contains_internal_protocol
from .knowledge_references import KnowledgeReferences
from .request_diagnostics import MessageRequests
from .schemas import MessageTiming

_PRIVATE_THOUGHT = re.compile(r"<(think|thinking)\b[^>]*>.*?(?:</\1\s*>|$)", re.IGNORECASE | re.DOTALL)


def _visible_public_reply(content: str) -> str:
    content = unwrap_assistant_message(content).strip()
    candidate = content
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        # A failed structured reply is not a transcript. In particular, do
        # not salvage its first string and accidentally include private fields
        # following it in a truncated historical transport envelope.
        if re.match(r"^\s*(?:```json\s*)?\{", content) and re.search(r'"chat_reply"\s*:', content):
            return ""
    else:
        if isinstance(payload, dict) and "chat_reply" in payload:
            content = payload["chat_reply"] if isinstance(payload["chat_reply"], str) else ""
    content = _PRIVATE_THOUGHT.sub("", unwrap_assistant_message(content)).strip()
    return "" if contains_internal_protocol(content) else content


class SharedMessage(BaseModel):
    # Keep this public allowlist independent of private conversation schemas:
    # later additions to those must not silently become public share fields.
    # The id is snapshot-local, never a live message API identifier.
    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime | None = None
    @model_validator(mode="after")
    def visible_reply_only(self):
        if self.role == "assistant":
            # Never promote private reasoning into an empty public reply.
            self.content = _visible_public_reply(self.content)
        return self


class ConversationShareSnapshot(BaseModel):
    snapshot_version: Literal[1] = 1
    title: str
    created_at: datetime
    messages: list[SharedMessage] = Field(default_factory=list)


class AdminSharedMessage(SharedMessage):
    reasoning_content: str | None = None
    model_name: str | None = None
    routing_reasoning_content: str | None = None
    router_model_name: str | None = None
    timing: MessageTiming | None = None
    knowledge_references: KnowledgeReferences | None = None
    request_records: MessageRequests | None = None


class AdminConversationShareSnapshot(BaseModel):
    # Only server-authorized admin publication creates this version. Legacy
    # snapshots remain transcript-only regardless of the owner's current role.
    snapshot_version: Literal[2] = 2
    title: str
    created_at: datetime
    messages: list[AdminSharedMessage] = Field(default_factory=list)


ShareSnapshot = Annotated[
    ConversationShareSnapshot | AdminConversationShareSnapshot,
    Field(discriminator="snapshot_version"),
]


class ConversationShareCreated(BaseModel):
    id: str
    title: str
    created_at: datetime
    message_count: int
    snapshot_version: Literal[1, 2] = 1
    token: str
    path: str
