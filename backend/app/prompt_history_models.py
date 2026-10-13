"""Append-only prompt snapshots and per-prompt serialization state."""
from datetime import datetime
from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, UTCDateTime

class PromptHistoryState(Base):
    __tablename__ = 'prompt_history_state'
    prompt_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    default_content: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), 'mysql'), nullable=False)
    pending_actor: Mapped[str | None] = mapped_column(String(128))
    pending_action: Mapped[str | None] = mapped_column(String(32))
    pending_restore: Mapped[int | None] = mapped_column(Integer)

class PromptRevision(Base):
    __tablename__ = 'prompt_revisions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prompt_key: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), 'mysql'), nullable=False)
    is_overridden: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    restored_from: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint('prompt_key', 'revision', name='uq_prompt_revision'),)
