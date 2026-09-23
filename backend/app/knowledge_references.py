"""Turn-specific reference snapshots; never fed back into model context."""
from __future__ import annotations

import math
from pydantic import BaseModel, Field


class ReferenceChunk(BaseModel):
    id: str
    source: str
    text: str
    score: float | None = None
    score_type: str | None = None


class KnowledgeReferences(BaseModel):
    version: int = 1
    available: bool = False
    module: str | None = None
    retrieval_outcome: str | None = None
    gate_reason: str | None = None
    mediator_status: str | None = None
    mediator_reason: str | None = None
    mediator_reasoning_content: str | None = None
    mediator_guidance: str | None = None
    mediator_cautions: list[str] = Field(default_factory=list)
    mediator_model: str | None = None
    mediator_duration_ms: int | None = None
    context_withheld: bool = False
    validator_status: str | None = None
    recalled: list[ReferenceChunk] = Field(default_factory=list)
    provided: list[ReferenceChunk] = Field(default_factory=list)


def reference_snapshot(*, module, recalled, provided, retrieval, mediator, context_withheld=False, mediator_reasoning=None):
    def project(chunks):
        return [ReferenceChunk(id=str(c.id), source=c.source, text=c.text,
            score=c.score if c.score is not None and math.isfinite(c.score) else None,
            score_type=c.score_type) for c in chunks]
    return KnowledgeReferences(
        version=3, available=True, module=module,
        retrieval_outcome=retrieval.get("outcome", "disabled"),
        gate_reason=(retrieval.get("gate") or {}).get("reason"),
        mediator_status=mediator.get("status"), mediator_reason=mediator.get("reason"),
        mediator_reasoning_content=mediator_reasoning if recalled else None,
        # Store only the validated advice actually available to the module.
        # Historical v1/v2 snapshots remain empty: never regenerate guidance.
        mediator_guidance=(mediator.get("guidance") if recalled and not context_withheld
                           and mediator.get("status") == "completed" else None),
        mediator_cautions=(mediator.get("cautions") or [] if recalled and not context_withheld
                           and mediator.get("status") == "completed" else []),
        mediator_model=mediator.get("model"), mediator_duration_ms=mediator.get("duration_ms"),
        context_withheld=context_withheld,
        recalled=project(recalled), provided=project(provided),
    ).model_dump(mode="json")
