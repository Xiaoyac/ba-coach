"""Public shares contain only visible transcript fields, including legacy links."""
from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .assistant_content import unwrap_assistant_message
from .reasoning import contains_internal_protocol

_PRIVATE_THOUGHT = re.compile(r"<(think|thinking)\b[^>]*>.*?(?:</\1\s*>|$)", re.IGNORECASE | re.DOTALL)


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
            self.content = _PRIVATE_THOUGHT.sub("", unwrap_assistant_message(self.content)).strip()
            if contains_internal_protocol(self.content):
                self.content = ""
        return self


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
