"""Request / response models for the public API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

Role = Literal["user", "assistant"]

# The two rating ranges used by the assessment. Named so the range lives in one
# place — the 0–5 per-activity scores and the 0–10 daily summary are easy to
# transpose by accident, and a swapped bound would silently truncate answers.
Score5 = Annotated[int, Field(ge=0, le=5)]
Score10 = Annotated[int, Field(ge=0, le=10)]


class Message(BaseModel):
    role: Role
    content: str
    reasoning_content: str | None = None
    model_name: str | None = None
    routing_reasoning_content: str | None = None
    router_model_name: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=20_000)
    # Omit on the first turn — the server mints one and returns it.
    session_id: str | None = None
    # Override the server default for this request ("claude" | "deepseek").
    provider: Literal["claude", "deepseek", "doubao"] | None = None
    # Pin a module and skip routing entirely, e.g. "module_3".
    module: str | None = None
    # Free-form context carried from the frontend (user profile, scenario, …).
    # Available to the prompt builder; nothing here is trusted as instructions.
    metadata: dict[str, str] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    reasoning_content: str = ""
    routing_reasoning_content: str = ""
    router_model_name: str = ""
    provider: str
    model: str
    # The module that generated this reply. It never changes after the reply
    # has been produced, even if the post-hoc router advances the session.
    reply_module: str
    # The module that will handle the next turn. Ordinary successful replies
    # are routed asynchronously, so this is null while `routing_pending` is
    # true; the conversation snapshot publishes the eventual value.
    next_module: str | None = None
    routing_pending: bool = False
    # How reply_module was selected (explicit | sticky | default).
    routed_by: str
    usage: dict[str, int] = Field(default_factory=dict)


class SessionInfo(BaseModel):
    session_id: str
    message_count: int
    next_module: str | None
    # Durable facts the graph carries across turns.
    memory: dict[str, str] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    providers: dict[str, bool]  # provider -> configured?


# ---------------------------------------------------------------------------
# Daily Behavioral Activation Assessment
# ---------------------------------------------------------------------------


class ActivityLogIn(BaseModel):
    """One activity card from step 1 of the wizard."""

    # Free text: "早上", "午饭后" and "09:00" are all valid answers, and
    # demanding a parseable time would reject the honest one. The frontend
    # narrows this to a dropdown of common periods, but the API itself only
    # requires *something* non-empty — a future client isn't forced into the
    # same fixed list.
    time_slot: str = Field(..., min_length=1, max_length=64)
    activity: str = Field(..., min_length=1, max_length=500)

    emotion: Score5
    achievement: Score5
    connection: Score5
    enjoyment: Score5
    importance: Score5

    note: str | None = Field(default=None, max_length=2000)


class ActivityLogOut(ActivityLogIn):
    position: int


class DailySummaryIn(BaseModel):
    """The 0–10 sliders from step 2 of the wizard."""

    completion_rate: Score10
    activity_level: Score10
    social_connection: Score10
    approach_vs_avoidance: Score10
    overall_mood: Score10

    reflection_note: str | None = Field(default=None, max_length=4000)


class AssessmentSubmission(BaseModel):
    """POST /api/assessment — a completed assessment for one local day."""

    # Resolved server-side from the caller's timezone when omitted. Accepted
    # explicitly only so a client can be unambiguous about which day it means.
    local_date: date | None = None
    # IANA name, e.g. "Asia/Shanghai". Unknown values fall back to UTC.
    timezone: str | None = Field(default=None, max_length=64)

    # A day with no activities logged is a skip, not a submission — the POST
    # body has to carry at least one card. The cap is a denial-of-service
    # bound, not a clinical judgement.
    activities: list[ActivityLogIn] = Field(..., min_length=1, max_length=50)
    summary: DailySummaryIn


class AssessmentSkip(BaseModel):
    """POST /api/assessment/skip — asked, and declined, for one local day."""

    local_date: date | None = None
    timezone: str | None = Field(default=None, max_length=64)


class AssessmentStatus(BaseModel):
    """GET /api/assessment/status — what the app shell gates on."""

    # The local date the answer is about, and the zone it was resolved in.
    local_date: date
    timezone: str

    has_completed_today: bool
    has_skipped_today: bool
    # Derived: false when the day is either done or explicitly skipped. The
    # frontend should gate on this rather than re-deriving the rule, so
    # "when do we ask?" has exactly one definition.
    should_prompt: bool


class AssessmentOut(BaseModel):
    """A stored assessment, read back."""

    id: int
    local_date: date
    timezone: str
    status: Literal["completed", "skipped"]

    completion_rate: int | None = None
    activity_level: int | None = None
    social_connection: int | None = None
    approach_vs_avoidance: int | None = None
    overall_mood: int | None = None
    reflection_note: str | None = None

    activities: list[ActivityLogOut] = Field(default_factory=list)


class AssessmentHistoryPage(BaseModel):
    """A bounded, newest-first page of completed daily records."""

    items: list[AssessmentOut] = Field(default_factory=list)
    has_more: bool
    next_offset: int | None = None


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

# Mirrors the ENUM/SET definitions on `user_profile` in models_business.py.
# Declared as Literals so a bad value is a 422 at the edge rather than a MySQL
# "Data truncated for column" error deep inside a transaction — and so the
# frontend can render the exact option list from one source.
CommunicationPreference = Literal["直接明了", "温柔引导", "理性分析", "轻松幽默"]
PhysicalCondition = Literal[
    "膝关节损伤", "腰背酸痛", "慢性疼痛", "易疲劳", "睡眠障碍",
    "偏头痛", "哮喘", "眩晕", "鼻炎", "术后恢复期",
]
BehaviorTaboo = Literal[
    "不能剧烈运动", "不能久站", "不能晒太阳", "怕吵闹", "怕人多",
    "怕拥挤闭塞的地方", "不坐公共交通",
]
ContentTaboo = Literal[
    "不谈工作", "不谈学习", "不谈家庭", "不谈身材外貌", "不谈感情",
    "不谈未来计划", "不喜欢被比较", "反感正能量说教", "不喜欢被经常催促",
]
LivingStatus = Literal["独居", "和家人", "和朋友", "和恋人"]
SupporterRelation = Literal["父母", "恋人", "子女", "朋友", "兄弟姐妹", "同事"]
SupporterInfluence = Literal["弱", "中", "强"]
ReminderFrequency = Literal[
    "每天一次", "隔天一次", "每三天一次", "每周一次",
    "仅在我主动找你时提醒", "暂时不需要提醒",
]
ReminderTimeSlot = Literal[
    "早晨7-9", "上午9-12", "中午12-14", "下午14-18", "傍晚18-21", "晚上21-23",
]
ActivityEnvironment = Literal["室内", "户外", "都可以"]
ActivitySocial = Literal["独自", "一对一", "群体", "都可以"]
ActivityIntensity = Literal["安静", "热闹", "都可以"]
ModelProvider = Literal["deepseek", "doubao"]
PromptKey = Literal[
    "global", "module_1", "module_2", "module_3", "module_4", "router_agent"
]


class AdminPromptItem(BaseModel):
    key: PromptKey
    label: str
    description: str
    content: str
    is_overridden: bool
    updated_by: str | None = None
    updated_at: datetime | None = None


class AdminPromptBundle(BaseModel):
    prompts: list[AdminPromptItem]


class AdminPromptUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=100_000)


KnowledgeCategory = Literal["BA", "PA", "BCT", "MI"]


class AdminKnowledgeImport(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    category: KnowledgeCategory
    markdown: str = Field(..., min_length=1, max_length=2_000_000)


class AdminKnowledgeSourceItem(BaseModel):
    id: int
    name: str
    category: KnowledgeCategory
    modules: list[str]
    chunk_count: int
    content_hash: str
    updated_by: str
    updated_at: datetime
    unchanged: bool = False


class AdminKnowledgeBundle(BaseModel):
    sources: list[AdminKnowledgeSourceItem]


SandboxModule = Literal["module_1", "module_2", "module_3", "module_4"]


class AdminSandboxStartRequest(BaseModel):
    """Start an isolated administrator-only conversation at one module."""

    module: SandboxModule


class RegisterRequest(BaseModel):
    """POST /api/auth/register.

    Carries two kinds of field, on purpose:

    * things that do not really change (age, living situation), which are
      cheapest to ask once and never again; and
    * the two safety constraints (physical limits, movement taboos) plus the
      tone preference, which *do* change — but which the coach needs from its
      very first reply, so a blank profile would mean the first session runs
      without them.

    Everything here is editable afterwards through `PATCH /api/profile`. A
    healed injury or a changed preference must not be frozen at signup.
    """

    username: str = Field(..., min_length=3, max_length=32)
    # Length is the only rule enforced. Composition rules ("one digit, one
    # symbol") measurably push people toward predictable patterns without
    # adding entropy; NIST SP 800-63B advises a length floor instead.
    password: str = Field(..., min_length=8, max_length=128)
    email: EmailStr

    nickname: str = Field(..., min_length=1, max_length=58)
    tag: str = Field(..., min_length=5, max_length=5, pattern=r"^[0-9]{5}$")
    # TINYINT UNSIGNED on the column. The upper bound is a typo guard, not a
    # claim about human lifespans.
    age: int | None = Field(default=None, ge=10, le=120)
    living_status: LivingStatus | None = None
    communication_preference: CommunicationPreference | None = None
    # SET columns: any combination, including none at all.
    physical_condition: list[PhysicalCondition] = Field(default_factory=list)
    behavior_taboo: list[BehaviorTaboo] = Field(default_factory=list)

    @field_validator("username")
    @classmethod
    def validate_login_username(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("登录账号至少需要 3 个字符")
        if not value.isascii() or not value.isalnum():
            raise ValueError("登录账号只能包含英文字母和数字")
        return value

    @field_validator("nickname")
    @classmethod
    def validate_display_nickname(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("昵称不能为空")
        if "#" in value:
            raise ValueError("昵称不能包含 #")
        return value


class Supporter(BaseModel):
    """One person the subject can realistically do something with.

    `relation` is free text, not the six-value ENUM `user_profile` uses: the
    point of the extension table is that "室友" and "教练" are real answers.
    The legacy columns still get the first two whose relation happens to be one
    of the six — see `routes/profile.py`.
    """

    relation: str = Field(..., min_length=1, max_length=32)
    nickname: str | None = Field(default=None, max_length=64)
    influence: SupporterInfluence | None = None


class ReminderWindow(BaseModel):
    """A precise "from X to Y" the six-bucket ENUM cannot express."""

    # Minutes from midnight. 0–1439; the handler rejects start >= end rather
    # than silently storing a window that never opens.
    start_minute: int = Field(..., ge=0, le=1439)
    end_minute: int = Field(..., ge=0, le=1439)


class ProfileOut(BaseModel):
    """GET /api/profile — everything the subject may see and change."""

    nickname: str | None = None
    tag: str | None = None
    display_id: str | None = None
    age: int | None = None
    living_status: LivingStatus | None = None

    has_supporter: bool = False
    supporter1_relation: SupporterRelation | None = None
    supporter1_nickname: str | None = None
    supporter1_influence: SupporterInfluence | None = None
    supporter2_relation: SupporterRelation | None = None
    supporter2_nickname: str | None = None
    supporter2_influence: SupporterInfluence | None = None

    communication_preference: CommunicationPreference | None = None
    reminder_frequency: ReminderFrequency | None = None
    reminder_time_slot: ReminderTimeSlot | None = None

    physical_condition: list[PhysicalCondition] = Field(default_factory=list)
    behavior_taboo: list[BehaviorTaboo] = Field(default_factory=list)
    content_taboo: list[ContentTaboo] = Field(default_factory=list)

    activity_environment: ActivityEnvironment | None = None
    activity_social: ActivitySocial | None = None
    activity_intensity: ActivityIntensity | None = None

    # From `profile_extensions` — richer than the legacy columns above can
    # hold, and what the UI actually edits. `supporter1_*`/`supporter2_*` and
    # `reminder_time_slot` remain as the coarse projection of these.
    supporters: list[Supporter] = Field(default_factory=list)
    reminder_window: ReminderWindow | None = None

    # App-level preference (not part of the externally-owned clinical profile).
    # Availability lets the UI show a configured choice and a clear
    # "待配置" state instead of allowing a selection that will fail later.
    preferred_provider: ModelProvider = "deepseek"
    available_providers: dict[str, bool] = Field(default_factory=dict)

    # Read-only, shown for orientation. `current_module` is the programme's
    # own state machine and `risk_level` is a clinical judgement — neither is
    # something the subject sets about themselves, so `ProfileUpdate` below
    # does not accept them.
    current_module: str | None = None


class ProfileUpdate(BaseModel):
    """PATCH /api/profile. Omitted fields are left alone; null clears one.

    The distinction matters: a form that only edits one section must not wipe
    the others. The handler applies `model_dump(exclude_unset=True)`, so
    "absent" and "explicitly set to null" are genuinely different requests —
    the first is a no-op for that column, the second empties it.

    Deliberately *not* here: `current_module`, `module1_done_flag`,
    `risk_level`, `expression_style`. The first two are programme state the
    router owns, and letting a subject POST their own `current_module` would
    let them skip the gating that module 1 exists to enforce. The last two are
    assessments *about* someone, not statements *by* them.
    """

    nickname: str | None = Field(default=None, min_length=1, max_length=58)
    tag: str | None = Field(
        default=None, min_length=5, max_length=5, pattern=r"^[0-9]{5}$"
    )
    age: int | None = Field(default=None, ge=10, le=120)
    living_status: LivingStatus | None = None

    has_supporter: bool | None = None
    supporter1_relation: SupporterRelation | None = None
    supporter1_nickname: str | None = Field(default=None, max_length=64)
    supporter1_influence: SupporterInfluence | None = None
    supporter2_relation: SupporterRelation | None = None
    supporter2_nickname: str | None = Field(default=None, max_length=64)
    supporter2_influence: SupporterInfluence | None = None

    communication_preference: CommunicationPreference | None = None
    reminder_frequency: ReminderFrequency | None = None
    reminder_time_slot: ReminderTimeSlot | None = None

    physical_condition: list[PhysicalCondition] | None = None
    behavior_taboo: list[BehaviorTaboo] | None = None
    content_taboo: list[ContentTaboo] | None = None

    activity_environment: ActivityEnvironment | None = None
    activity_social: ActivitySocial | None = None
    activity_intensity: ActivityIntensity | None = None

    # Any number of supporters, any relation the subject types. Sending this
    # replaces the whole list — editing one entry means sending the list back
    # with that entry changed, which is also how a removal is expressed.
    supporters: list[Supporter] | None = Field(default=None, max_length=10)
    reminder_window: ReminderWindow | None = None
    preferred_provider: ModelProvider | None = None


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    """POST /api/auth/password — change your own password while signed in.

    The current password is required even though the caller already holds a
    valid token. A token can be lying around on a borrowed or unlocked device;
    demanding the password proves the person at the keyboard is the account
    holder, not just whoever the browser was left signed in as.
    """

    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class EmailAddressRequest(BaseModel):
    """Attach or replace the signed-in account's recovery email."""

    email: EmailStr


class EmailTokenRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(EmailTokenRequest):
    new_password: str = Field(..., min_length=8, max_length=128)


class MessageResponse(BaseModel):
    message: str


class AdminResetRequest(BaseModel):
    """POST /api/auth/admin/reset — operator-initiated password reset.

    Unlike `ChangePasswordRequest` this needs no current password and no
    signed-in session; the operator key stands in for both. That is precisely
    why the key has to be long, random, and kept out of source — see
    `Settings.admin_reset_key`.
    """

    admin_key: str = Field(..., min_length=1, max_length=256)
    username: str = Field(..., min_length=1, max_length=64)
    new_password: str = Field(..., min_length=8, max_length=128)


class AccountInfo(BaseModel):
    """GET /api/auth/me — who the caller is, for the app shell."""

    username: str
    nickname: str | None = None
    tag: str | None = None
    display_id: str | None = None
    # `user_profile.uuid`. The frontend does not need it, but it is what every
    # clinical table keys on, so surfacing it makes support questions ("which
    # row is mine?") answerable without a database session.
    profile_uuid: str
    current_module: str | None = None
    role: Literal["user", "admin"] = "user"
    email: EmailStr | None = None
    email_verified: bool = False
    email_required: bool = True
    email_delivery_available: bool = False


class AdminAccountItem(BaseModel):
    id: int
    username: str
    nickname: str | None = None
    tag: str | None = None
    display_id: str | None = None
    role: Literal["user", "admin"]
    created_at: datetime
    last_login_at: datetime | None = None


class AdminAccountList(BaseModel):
    accounts: list[AdminAccountItem]


class AdminRoleGrant(BaseModel):
    role: Literal["admin"]


class IssueReportCreate(BaseModel):
    """User-authored report plus diagnostics captured by the web client."""

    description: str = Field(..., min_length=3, max_length=5_000)
    screenshot_data_url: str | None = Field(default=None, max_length=3_000_000)
    page_url: str = Field(default="", max_length=2_048)
    session_id: str | None = Field(default=None, max_length=64)
    last_error: str | None = Field(default=None, max_length=1_000)
    user_agent: str = Field(default="", max_length=512)
    viewport_width: int | None = Field(default=None, ge=1, le=20_000)
    viewport_height: int | None = Field(default=None, ge=1, le=20_000)
    client_online: bool | None = None

    @field_validator("description")
    @classmethod
    def strip_issue_description(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("请至少写 3 个字符")
        return value


class IssueReportCreated(BaseModel):
    id: int
    created_at: datetime


class AdminIssueReportItem(BaseModel):
    id: int
    username: str
    display_name: str | None = None
    description: str
    status: Literal["open", "resolved"]
    has_screenshot: bool
    screenshot_mime: str | None = None
    page_url: str
    session_id: str | None = None
    last_error: str | None = None
    user_agent: str
    viewport_width: int | None = None
    viewport_height: int | None = None
    client_online: bool | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class AdminIssueReportList(BaseModel):
    reports: list[AdminIssueReportItem]


class AdminIssueReportStatusUpdate(BaseModel):
    status: Literal["open", "resolved"]


class AuthResponse(BaseModel):
    """A successful register/login. The only place a raw token is ever emitted."""

    token: str
    expires_at: datetime
    account: AccountInfo


# ---------------------------------------------------------------------------
# Conversation sidebar
# ---------------------------------------------------------------------------


class ConversationSummary(BaseModel):
    """One row in the sidebar's conversation list."""

    session_id: str
    title: str
    updated_at: datetime
    pinned: bool = False


class ConversationDetail(ConversationSummary):
    """A conversation's full transcript, for switching into it."""

    messages: list[Message] = Field(default_factory=list)
    # Durable pointer for the next turn. This is deliberately not called
    # `module`: the latest assistant reply may belong to the previous module.
    next_module: str | None = None


class AdminSandboxConversation(ConversationDetail):
    """A fresh, empty conversation pinned to a selected module."""

    next_module: SandboxModule


class ConversationUpdate(BaseModel):
    """PATCH body for renaming and/or pinning a conversation.

    Both fields are optional and `None` means "leave alone", so a rename and a
    pin toggle are the same endpoint without one clobbering the other. An
    all-`None` body is rejected in the handler rather than silently no-oping.
    """

    # Same 80-char ceiling as the column. min_length=1 after the handler
    # strips it — a title that is only whitespace is a mistake, not a rename.
    title: str | None = Field(default=None, max_length=80)
    pinned: bool | None = None
