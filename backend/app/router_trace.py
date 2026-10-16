"""Allowlisted Router wire records for the existing administrator debug API."""
from typing import Literal
from pydantic import BaseModel, Field

class RouterMessage(BaseModel):
    role: Literal['system', 'user', 'assistant']
    content: str

class RouterRequest(BaseModel):
    model: str
    messages: list[RouterMessage] = Field(default_factory=list)
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    thinking: dict[str, str] | None = None
    temperature: float | None = None

class RouterResponse(BaseModel):
    model: str | None = None
    content: str | None = None
    reasoning_content: str | None = None
    finish_reason: str | None = None
    error_code: str | None = None

class RouterCall(BaseModel):
    request: RouterRequest
    response: RouterResponse | None = None
    request_id: str | None = None
    http_status: int | None = None
    duration_ms: int | None = None


def project_router_calls(value):
    if not isinstance(value, list):
        return []
    result = []
    for call in value:
        try:
            result.append(RouterCall.model_validate(call))
        except (ValueError, TypeError):
            continue
    return result
