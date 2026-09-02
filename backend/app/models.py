"""ORM models: the daily assessment and the conversation transcript log.

    assessment_entries   one row per subject per local date — the daily summary
      └── activity_logs  the individual activities logged for that day

    conversations             one row per chat session, for the sidebar
      └── conversation_messages   its full transcript, append-only

On identity: this app has no auth and no user table, so `subject_id` is
whatever opaque id the client presents (today: a UUID the browser generates
once and keeps in localStorage). It is deliberately named `subject_id` rather
than `user_id` because it is NOT authenticated — anyone who learns an id can
read and write that subject's rows. When real accounts arrive, backfill this
column from the user table and add the foreign key; nothing else in the schema
has to move. See `app.identity`.

On dates: `recorded_on` is the subject's *local* date, not UTC. "Have I done
today's assessment?" is a question about the wall clock the person is looking
at, and for anyone west of UTC the two disagree for several hours every day.
The timezone the date was resolved in is stored alongside it so a later
timezone change is auditable rather than silently rewriting history.

On the conversation log: it is deliberately separate from `SessionStore`
(app/session.py), which is the *live*, in-memory history the LangGraph agent
reads on every turn. That store trims to `max_history_messages` for the
model's token budget and does not survive a restart — neither property is
acceptable for a "browse your past conversations" list. This table is written
by `app.conversation_store.record_turn` as a side effect of each turn, mirrors
the same two messages the graph already decided to keep, and never trims.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.mysql import LONGTEXT, MEDIUMBLOB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime

# Status values for AssessmentEntry.status.
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssessmentEntry(Base):
    """One subject's assessment for one local date."""

    __tablename__ = "assessment_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_on: Mapped[date] = mapped_column(Date, nullable=False)
    # IANA name (e.g. "Asia/Shanghai") that `recorded_on` was computed in.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    # "completed" carries a full summary; "skipped" records that the person was
    # asked and declined, which is what stops the modal re-prompting all day.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_COMPLETED
    )

    # ---- Daily summary, 0–10. Null on a skipped entry. -------------------
    completion_rate: Mapped[int | None] = mapped_column(Integer)
    activity_level: Mapped[int | None] = mapped_column(Integer)
    social_connection: Mapped[int | None] = mapped_column(Integer)
    approach_vs_avoidance: Mapped[int | None] = mapped_column(Integer)
    overall_mood: Mapped[int | None] = mapped_column(Integer)

    reflection_note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )

    activities: Mapped[list[ActivityLog]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="ActivityLog.position",
        lazy="selectin",
    )

    __table_args__ = (
        # The one-per-day rule, enforced by the database rather than only by
        # the handler — two concurrent submits would otherwise both pass an
        # application-level "does it exist?" check and write duplicate days.
        UniqueConstraint("subject_id", "recorded_on", name="uq_entry_subject_date"),
        Index("ix_entry_subject_date", "subject_id", "recorded_on"),
        CheckConstraint(
            f"status IN ('{STATUS_COMPLETED}', '{STATUS_SKIPPED}')",
            name="ck_entry_status",
        ),
        *[
            CheckConstraint(f"{col} IS NULL OR ({col} BETWEEN 0 AND 10)", name=f"ck_entry_{col}")
            for col in (
                "completion_rate",
                "activity_level",
                "social_connection",
                "approach_vs_avoidance",
                "overall_mood",
            )
        ],
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AssessmentEntry {self.subject_id[:8]}… {self.recorded_on} "
            f"{self.status}>"
        )


class ActivityLog(Base):
    """A single logged activity within a day's assessment."""

    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_entries.id", ondelete="CASCADE"), nullable=False
    )

    # Display order of the cards, so a re-read reproduces what the user built.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Free text rather than a time column: people log "早上" or "午饭后" as
    # readily as "09:00", and forcing a parse would reject the honest answer.
    time_slot: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    activity: Mapped[str] = mapped_column(String(500), nullable=False)

    # ---- Per-activity ratings, 0–5. -------------------------------------
    emotion: Mapped[int] = mapped_column(Integer, nullable=False)
    achievement: Mapped[int] = mapped_column(Integer, nullable=False)
    connection: Mapped[int] = mapped_column(Integer, nullable=False)
    enjoyment: Mapped[int] = mapped_column(Integer, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False)

    note: Mapped[str | None] = mapped_column(Text)

    entry: Mapped[AssessmentEntry] = relationship(back_populates="activities")

    __table_args__ = (
        Index("ix_activity_entry", "entry_id"),
        *[
            CheckConstraint(f"{col} BETWEEN 0 AND 5", name=f"ck_activity_{col}")
            for col in ("emotion", "achievement", "connection", "enjoyment", "importance")
        ],
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ActivityLog {self.time_slot!r} {self.activity[:20]!r}>"


class UserAccount(Base):
    """Login credentials. App-owned — the 7 business tables carry none.

    This table exists because `user_profile` (and every other pre-existing
    table in `models_business.py`) has no username, no email and no password
    column: that schema was designed for a system where identity arrived from
    somewhere else. Rather than alter an externally-owned table, credentials
    live here and point *at* a profile.

    `profile_uuid` is the join to everything clinical: it holds the value of
    `user_profile.uuid`, which is what all six record tables key on as
    `user_id`. It is deliberately not a `ForeignKey` — `user_profile` is owned
    by another system and declares no foreign keys of its own (see
    `models_business` docstring), so claiming one here would be a constraint
    this database does not actually have.
    """

    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Independent login account. Registration accepts ASCII letters and
    # digits only and stores a case-folded value. It is never combined with a
    # nickname or display tag.
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Argon2id, in PHC string format: the algorithm, its cost parameters and
    # the per-password salt are all encoded in here. That is why there is no
    # salt column and why the length is generous — the format is allowed to
    # grow when parameters are raised.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    profile_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="account", cascade="all, delete-orphan", lazy="selectin"
    )
    settings: Mapped[AccountSettings | None] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    handle: Mapped[AccountHandle | None] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    email_address: Mapped[AccountEmail | None] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )

    __table_args__ = (Index("ix_account_username", "username"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<UserAccount {self.username!r}>"


class AccountHandle(Base):
    """User-chosen public ``nickname#12345`` identity.

    The database column names are retained from the first Riot-ID migration,
    but ``base_username`` now stores the display nickname, not the login
    account. Existing accounts may temporarily have no row while they choose a
    tag; every new registration creates one. The composite unique constraint
    is the final authority for nickname+tag uniqueness.
    """

    __tablename__ = "account_handles"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    base_username: Mapped[str] = mapped_column(String(58), nullable=False)
    normalized_base: Mapped[str] = mapped_column(String(58), nullable=False)
    tag: Mapped[str] = mapped_column(String(5), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )

    account: Mapped[UserAccount] = relationship(back_populates="handle")

    __table_args__ = (
        UniqueConstraint(
            "normalized_base", "tag", name="uq_account_handle_base_tag"
        ),
        Index("ix_account_handle_base", "normalized_base"),
    )

    @property
    def full_username(self) -> str:
        return f"{self.base_username}#{self.tag}"


class AccountSettings(Base):
    """App-owned account role and temporary model preference.

    Kept in a separate table so adding this feature does not ALTER the
    already-deployed ``user_accounts`` table.  ``create_all`` can safely add
    this missing table on startup; existing accounts simply read the defaults
    until they save a preference.
    """

    __tablename__ = "account_settings"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    preferred_provider: Mapped[str] = mapped_column(
        String(16), nullable=False, default="deepseek", server_default=text("'deepseek'")
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="user", server_default=text("'user'")
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )

    account: Mapped[UserAccount] = relationship(back_populates="settings")

    __table_args__ = (
        CheckConstraint(
            "preferred_provider IN ('deepseek', 'doubao')",
            name="ck_account_settings_provider",
        ),
        CheckConstraint("role IN ('user', 'admin')", name="ck_account_settings_role"),
    )


class AccountEmail(Base):
    """One recovery address per account, kept outside the deployed account table.

    Existing installations already have ``user_accounts`` rows and this
    project intentionally has no ALTER migration runner.  A one-to-one table
    lets ``create_all`` add the feature without rewriting that live table.
    Only a verified address may receive a password-reset link.
    """

    __tablename__ = "account_emails"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), primary_key=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    normalized_email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, onupdate=_utcnow,
        server_default=func.now()
    )

    account: Mapped[UserAccount] = relationship(back_populates="email_address")

    __table_args__ = (Index("ix_account_email_normalized", "normalized_email"),)


class AccountEmailToken(Base):
    """Hashed, expiring, single-use email verification/reset credential."""

    __tablename__ = "account_email_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index("ix_email_token_hash", "token_hash"),
        Index("ix_email_token_account_purpose", "account_id", "purpose"),
        CheckConstraint(
            "purpose IN ('verify_email', 'password_reset')",
            name="ck_account_email_token_purpose",
        ),
    )


class PromptOverride(Base):
    """Administrator-managed prompt text, falling back to source defaults.

    This is deliberately system-wide configuration, not account data: there
    is no owner/account foreign key, and ``prompt_key`` is globally unique.
    ``updated_by`` is audit metadata only — every administrator reads and
    edits the same row, and the resulting prompt applies to every subject.

    Only rows that differ from the source-controlled prompt need to exist.
    Deleting a row is therefore a safe, atomic "restore default" operation.
    ``LONGTEXT`` is used on MySQL so a future expanded prompt is not limited
    by the 64 KiB byte ceiling of ``TEXT``; SQLite keeps its normal Text type.
    """

    __tablename__ = "prompt_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_key: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    content: Mapped[str] = mapped_column(
        Text().with_variant(LONGTEXT(), "mysql"), nullable=False
    )
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )


class KnowledgeSourceRecord(Base):
    """One administrator-managed, system-wide knowledge-base document.

    Knowledge is shared configuration, just like prompt overrides: it is not
    owned by the administrator who imports it and is never scoped to a user.
    ``category`` controls which module may retrieve the document.
    """

    __tablename__ = "knowledge_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, onupdate=_utcnow,
        server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "category IN ('BA', 'PA', 'BCT', 'MI')",
            name="ck_knowledge_source_category",
        ),
    )


class KnowledgeChunkRecord(Base):
    """A bounded Markdown section searched and injected into one model turn."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    content: Mapped[str] = mapped_column(
        Text().with_variant(LONGTEXT(), "mysql"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source_id", "ordinal", name="uq_knowledge_chunk_order"),
        Index("ix_knowledge_chunk_source", "source_id"),
    )


class IssueReport(Base):
    """A user-submitted product problem with an optional viewport capture.

    Screenshots can contain conversation text, so they stay behind the same
    authenticated database boundary as the rest of the account data and are
    exposed only through administrator endpoints.  A binary column avoids the
    33% base64 expansion in storage; ``MEDIUMBLOB`` leaves room for the 2 MiB
    application limit without depending on MySQL's smaller BLOB ceiling.
    """

    __tablename__ = "issue_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default=text("'open'")
    )

    screenshot: Mapped[bytes | None] = mapped_column(
        LargeBinary().with_variant(MEDIUMBLOB(), "mysql"), nullable=True
    )
    screenshot_mime: Mapped[str | None] = mapped_column(String(32), nullable=True)

    page_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    viewport_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    viewport_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_online: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    __table_args__ = (
        Index("ix_issue_report_account_created", "account_id", "created_at"),
        Index("ix_issue_report_status_created", "status", "created_at"),
        CheckConstraint("status IN ('open', 'resolved')", name="ck_issue_report_status"),
    )


class AuthSession(Base):
    """One logged-in session — the server side of a bearer token.

    Only the SHA-256 of the token is stored, never the token itself. The token
    is high-entropy random rather than a password, so a fast hash is the right
    choice here (there is nothing to brute-force), but storing it hashed still
    means a leaked database dump does not hand the reader a set of live
    sessions they can replay.

    Sessions are rows rather than self-contained JWTs specifically so logout
    can actually revoke: deleting the row ends the session immediately, which
    a stateless signed token cannot do before its own expiry.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    account: Mapped[UserAccount] = relationship(back_populates="sessions")

    __table_args__ = (Index("ix_auth_session_token", "token_hash"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuthSession account={self.account_id} expires={self.expires_at}>"


class ProfileExtension(Base):
    """Profile answers the externally-owned `user_profile` cannot hold.

    Two of its columns are shaped for a narrower product than this one:

    * `reminder_time_slot` is an ENUM of six fixed buckets ("早晨7-9", …), so
      it cannot express "19:30 to 20:15".
    * supporters are two fixed slots, and `supporterN_relation` is an ENUM of
      six relations, so neither a third supporter nor "室友" can be stored.

    `user_profile` is owned by another system and must not be altered (see
    `app.models_business`), so the richer answers live here, in a table this
    app owns, keyed by the same `user_profile.uuid` everything else joins on.

    The legacy columns are still kept in sync as a lossy projection — the
    nearest ENUM bucket for the reminder, the first two supporters whose
    relation happens to be one of the six. Anything else reading
    `user_profile` therefore still sees a sensible, if coarser, answer rather
    than a stale one.
    """

    __tablename__ = "profile_extensions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Not a ForeignKey: `user_profile` is externally owned and declares none
    # of its own, so claiming one here would invent a constraint the database
    # does not actually have.
    profile_uuid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)

    # Minutes from midnight, 0–1439. Stored as integers rather than TIME so
    # "no answer" is simply NULL and arithmetic (ordering, overlap) needs no
    # date component attached to it.
    reminder_start_minute: Mapped[int | None] = mapped_column(Integer)
    reminder_end_minute: Mapped[int | None] = mapped_column(Integer)

    # [{"relation": str, "nickname": str, "influence": str}, …] — any length,
    # any relation the subject types. Validated in `routes/profile.py` before
    # it lands here.
    supporters: Mapped[list | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_profile_extension_uuid", "profile_uuid"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ProfileExtension {self.profile_uuid[:8]}…>"


class Conversation(Base):
    """One chat session, as shown in the sidebar."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # The same id `SessionStore` mints for this conversation (see session.py).
    # Reusing it — rather than inventing a separate conversation id — is what
    # lets the frontend use one identifier everywhere: it is both "which
    # LangGraph session do I continue" and "which sidebar row is this".
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    # Derived from the first user message, then editable — PATCH
    # /conversations/{session_id} lets the subject rename it. `record_turn`
    # only ever sets this when creating the row, so a rename is never
    # overwritten by a later turn.
    title: Mapped[str] = mapped_column(String(80), nullable=False, default="")

    # Pinned conversations sort above everything else, regardless of recency.
    # Stored as a flag rather than a manual sort order: there is no drag-to-
    # reorder in the sidebar, so a full ordering column would be state nothing
    # can currently produce.
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Monotonic durable revision. Unlike MAX(message.id), this also changes
    # when a background router enriches an existing assistant row.
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    # Bumped explicitly by `record_turn` on every turn, not left to `onupdate`:
    # a turn that only appends child `ConversationMessage` rows leaves this
    # row's own columns untouched, and SQLAlchemy's `onupdate` only fires when
    # an UPDATE is actually issued for the row itself. Sidebar order is by this
    # column, so a silently-stale value would sort conversations wrong.
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        # A user row reserves an even position and its eventual assistant row
        # uses the following odd position. The id is a deterministic fallback
        # for legacy duplicate positions written before concurrent locking.
        order_by=lambda: (ConversationMessage.position, ConversationMessage.id),
        lazy="selectin",
    )

    __table_args__ = (
        # Covers the sidebar's exact ORDER BY (pinned desc, updated_at desc).
        Index("ix_conversation_subject_pinned_updated", "subject_id", "pinned", "updated_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Conversation {self.session_id[:8]}… {self.title[:20]!r}>"


class ConversationMessage(Base):
    """One turn's worth of transcript. Append-only — never trimmed, never edited."""

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )

    # Display order; see `record_turn` for how it's assigned.
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Optional model thinking, kept separate from the visible answer. LONGTEXT
    # avoids truncating long reasoning traces from thinking-capable models.
    reasoning_content: Mapped[str | None] = mapped_column(
        Text().with_variant(LONGTEXT(), "mysql"), nullable=True
    )
    # Exact model that produced this assistant row. Storing it per message
    # keeps attribution correct after the account changes model preference.
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Post-hoc Router Agent reasoning is generated after the visible reply.
    routing_reasoning_content: Mapped[str | None] = mapped_column(
        Text().with_variant(LONGTEXT(), "mysql"), nullable=True
    )
    router_model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Per-stage operational telemetry. Nullable keeps pre-migration turns and
    # providers that do not expose a metric honest rather than inventing zero.
    risk_gate_duration_ms: Mapped[int | None] = mapped_column(Integer)
    time_to_first_reasoning_token_ms: Mapped[int | None] = mapped_column(Integer)
    time_to_first_content_token_ms: Mapped[int | None] = mapped_column(Integer)
    main_generation_duration_ms: Mapped[int | None] = mapped_column(Integer)
    router_duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    # A short SHA-256 fingerprint of the exact assembled system prompt. This
    # tracks source/admin prompt changes without storing sensitive prompt text.
    prompt_version: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_conversation_message_conv", "conversation_id"),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_conv_msg_role"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ConversationMessage {self.role} {self.content[:20]!r}>"


class ConversationRuntimeState(Base):
    """Durable graph state needed to resume the correct coaching workflow.

    Kept in a separate table so existing deployments can add it through
    ``create_all`` without altering the already-live ``conversations`` table.
    The full transcript remains in ``conversation_messages``; this row only
    stores the compact state that cannot be reconstructed reliably from prose.
    """

    __tablename__ = "conversation_runtime_states"

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    module: Mapped[str | None] = mapped_column(String(16), nullable=True)
    memory: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )


class ConversationModuleProgress(Base):
    """Query-friendly sub-step state for one conversation.

    The long module prompts are not durable state.  Keeping each module's
    completed step keys in explicit JSON columns lets the router carry
    evidence across turns without reconstructing completion from prose.
    """

    __tablename__ = "conversation_module_progress"

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    module_1_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    module_2_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    module_3_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    module_4_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active_cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, onupdate=_utcnow,
        server_default=func.now()
    )


class PACycle(Base):
    """One module-2 → module-3 → module-4 PA target lifecycle."""

    __tablename__ = "pa_cycles"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        UniqueConstraint("conversation_id", "ordinal", name="uq_pa_cycle_ordinal"),
        Index("ix_pa_cycle_subject_status", "subject_id", "status"),
    )


class ClinicalRecordCycleLink(Base):
    """Sidecar link from immutable business rows to a PA cycle.

    The seven business tables are externally owned and must not be altered,
    so their missing ``cycle_id`` is supplied here instead of with ALTER TABLE.
    """

    __tablename__ = "clinical_record_cycle_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(
        ForeignKey("pa_cycles.id", ondelete="CASCADE"), nullable=False
    )
    module: Mapped[str] = mapped_column(String(16), nullable=False)
    record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("module", "record_id", name="uq_clinical_record_cycle"),
        UniqueConstraint("cycle_id", "module", name="uq_cycle_module_record"),
        Index("ix_clinical_cycle_module", "cycle_id", "module"),
    )


class AIExecutionEvent(Base):
    """One normalised telemetry row for any LLM-backed stage."""

    __tablename__ = "ai_execution_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    assistant_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True
    )
    subject_id: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32))
    model_name: Mapped[str | None] = mapped_column(String(128))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric(14, 8))
    pricing_version: Mapped[str | None] = mapped_column(String(64))
    event_metadata: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_ai_event_session_stage", "session_id", "stage", "created_at"),
        Index("ix_ai_event_subject_created", "subject_id", "created_at"),
    )
