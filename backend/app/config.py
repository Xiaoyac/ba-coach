"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Provider = Literal["claude", "deepseek", "doubao"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- App ----------------------------------------------------------
    app_name: str = "psychology-ai-api"
    debug: bool = False
    # Disable for code-only releases that must not create tables or backfill data.
    startup_db_maintenance: bool = True
    database_schema_version: Literal["legacy", "v2"] = "legacy"
    # Lexical retrieval gates. Restart workers after changing these settings.
    knowledge_min_score: float = Field(default=0.1, ge=0, allow_inf_nan=False)
    knowledge_min_coverage: float = Field(default=0.12, ge=0, le=1)
    knowledge_relative_score: float = Field(default=0.2, ge=0, le=1)
    knowledge_max_per_source: int = Field(default=2, ge=1)
    knowledge_intent_gate_enabled: bool = True
    knowledge_mediator_enabled: bool = True
    answer_validator_enabled: bool = True
    knowledge_mediator_timeout_seconds: float = Field(default=12, ge=1, le=60)
    api_prefix: str = "/api"

    # Comma-separated in .env, e.g. "http://localhost:3000,https://app.example.com".
    # NoDecode stops pydantic-settings from trying to JSON-parse it first, which
    # would blow up before the validator below ever runs.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # Matched *in addition to* cors_origins — an exact list can't cover ngrok,
    # since its free tier hands out a fresh random subdomain per tunnel, so
    # there's no fixed URL to put in cors_origins ahead of time. Covers all
    # three of ngrok's current domain suffixes. Set to an empty string in .env
    # to turn this off entirely.
    cors_origin_regex: str | None = (
        r"^https://[a-z0-9-]+\.(ngrok-free\.app|ngrok\.io|ngrok\.app)$"
    )

    # ---- Provider selection -------------------------------------------
    default_provider: Provider = "claude"

    # ---- Module router -------------------------------------------------
    # Token cap for the router agent's call (app.router_agent) — a compact
    # JSON object, so this stays small. Shared with CLAUDE_ROUTER_MODEL /
    # DEEPSEEK_ROUTER_MODEL below, which is also the model that call uses.
    router_max_tokens: int = 256
    # Thinking-enabled module routing needs enough room for reasoning plus the
    # final JSON decision. Other router users keep the tighter budget above.
    router_reasoning_max_tokens: int = 1024
    # SummarizerNode (app/graph/nodes.py) also runs on the cheap router model
    # but writes a standing Memos entry rather than a one-shot routing
    # decision, so it gets a larger cap than a routing call needs.
    summarizer_max_tokens: int = 512
    # Clinical extraction needs far more room than the summariser: module 1
    # alone asks for 11 fields, several of them free text plus a nested
    # `abc_event` object. At 512 the reply was truncated mid-string on a
    # perfectly ordinary transcript, which parses as nothing at all — the
    # whole record silently came back empty. Sized for the largest module with
    # headroom; extraction is a background call, so the cost of the ceiling is
    # only paid on the tokens actually generated.
    extraction_max_tokens: int = 1800

    # Screen every turn for self-harm signals *before* answering, and divert
    # to the crisis response when one fires (see graph/nodes.risk_gate_node).
    # Costs one extra router-model call per turn — that is the price of the
    # check being able to change what the person is told rather than just
    # recording it afterwards. Turn off to get the latency back; the module
    # prompts keep their own risk-handling instructions either way.
    risk_gate_enabled: bool = True

    # ---- Claude --------------------------------------------------------
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5"
    # low | medium | high | xhigh | max — controls how much the model thinks.
    # "medium" is a good latency/quality balance for conversational use.
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_max_tokens: int = 8000
    # Routing model — must be fast and non-thinking. Haiku 4.5 rejects the
    # `effort` parameter, which is why classify() sends neither effort nor
    # thinking config.
    claude_router_model: str = "claude-haiku-4-5"

    # ---- DeepSeek ------------------------------------------------------
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"  # or "deepseek-reasoner"
    deepseek_max_tokens: int = 4000
    # Sampling temperature for the *conversational* call. Left unset, DeepSeek
    # defaults to 1.0, which is far too loose for this app: the module prompts
    # are a procedure with gates and ordering, and at 1.0 the model reads them
    # as tone rather than as rules — which is exactly what "it behaves like a
    # generic chat model" looks like from the outside. The router already
    # hardcodes 0 (it must be deterministic); this keeps the coach mostly
    # deterministic while leaving a little room for natural phrasing.
    deepseek_temperature: float = 0.3
    deepseek_router_model: str = "deepseek-chat"

    # ---- Doubao / Volcengine Ark --------------------------------------
    # Ark exposes an OpenAI-compatible chat-completions endpoint.  The model
    # id is deployment-specific, so there is deliberately no pretend default:
    # both this and the key must be configured before the UI enables Doubao.
    doubao_api_key: str | None = None
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    doubao_model: str | None = None
    doubao_max_tokens: int = 4000
    doubao_temperature: float = 0.3
    # Keep native thinking enabled, but ask Seed 2.1 to spend less time on
    # ordinary coaching turns.  Sent through Ark's OpenAI-compatible
    # ``extra_body`` so older OpenAI SDK type definitions cannot discard it.
    doubao_reasoning_effort: Literal["low", "medium", "high"] = "low"
    doubao_router_model: str | None = None

    # ---- Session store -------------------------------------------------
    # Number of *messages* (user + assistant) kept per session before the
    # oldest ones are dropped. 20 ≈ 10 exchanges.
    max_history_messages: int = 40
    session_ttl_seconds: int = 60 * 60 * 6  # 6 hours
    redis_url: str | None = None  # if set, use Redis instead of in-memory

    # ---- Accounts ------------------------------------------------------
    # How long a login lasts before the token stops resolving. Distinct from
    # `session_ttl_seconds` above, which is the *conversation* store's TTL —
    # they answer different questions ("is this person still signed in?" vs
    # "is this chat still warm?") and a shared value would couple them.
    # 30 days: this is a coaching tool people return to over weeks, and a
    # weekly forced re-login is friction with no matching threat.
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 30

    # ---- Account email / password recovery ---------------------------
    # Links open on a deliberately separate public hostname. The same Next.js
    # build serves it, but keeping recovery links on a named subdomain makes
    # their purpose clear and lets the host be isolated later without changing
    # the emails or API contract.
    account_link_base_url: str = "https://reset.bacoach.xyz"
    email_verification_ttl_seconds: int = 60 * 60 * 24
    password_reset_ttl_seconds: int = 60 * 60
    email_token_cooldown_seconds: int = 60

    # Generic SMTP works with Aliyun DirectMail, Tencent enterprise mail,
    # Resend SMTP, or any ordinary provider. Empty host/from disables delivery
    # safely; the public forgot-password endpoint still returns its neutral
    # response so it never becomes an account-enumeration oracle.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_from_name: str = "BA行为激活教练"
    smtp_starttls: bool = True
    smtp_use_ssl: bool = False

    # Operator key for POST /api/auth/admin/reset — resets any account's
    # password without knowing the old one.
    #
    # Unset (the default) disables that endpoint entirely: it answers 404, the
    # same as a URL that was never routed. That ordering matters — a fresh
    # deploy has no administrative back door at all until someone deliberately
    # puts a key here, rather than shipping one and hoping it gets changed.
    #
    # It lives in .env and never in source: a key in a source file is in every
    # copy of the repository, every backup and every screen-share, and cannot
    # be rotated without a code change. Minimum length is enforced at use time
    # (see routes/auth.py) because this value is reachable from the public
    # internet, so a short or guessable one is a key to every account's
    # clinical record.
    admin_reset_key: str | None = None

    # ---- Database ------------------------------------------------------
    # Assessment records live here. SQLite keeps local dev dependency-free;
    # point this at postgresql+asyncpg://… for anything real.
    database_url: str = "sqlite+aiosqlite:///./psychology.db"
    # Emit SQL to the log. Noisy — debugging only.
    database_echo: bool = False

    # Optional, deployment-owned model prices. Prices change and differ by
    # endpoint, so source code must not hard-code them. Example:
    # {"deepseek-reasoner":{"input":0.55,"output":2.19,"reasoning":2.19}}
    # Values are USD per million tokens; unmatched models keep cost NULL.
    llm_price_per_million_json: str = "{}"

    # ---- Memos (long-term memory) --------------------------------------
    # A self-hosted service, so there is no default URL — must be set.
    memos_api_key: str | None = None
    memos_base_url: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
