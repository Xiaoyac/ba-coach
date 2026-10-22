"""Request / response models for the public API."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, PrivateAttr, field_validator, model_validator

Role = Literal["user", "assistant"]

# The two rating ranges used by the assessment. Named so the range lives in one
# place — the 0–5 per-activity scores and the 0–10 daily summary are easy to
# transpose by accident, and a swapped bound would silently truncate answers.
Score5 = Annotated[int, Field(ge=0, le=5)]
Agree5 = Annotated[int, Field(ge=1, le=5)]
ActivityCount = Annotated[int, Field(ge=0, le=20)]
ActivityMinutes = Annotated[int, Field(ge=0, le=480)]
SleepHours = Annotated[float, Field(ge=0, le=24)]
SleepQuality = Annotated[int, Field(ge=1, le=4)]
Score10 = Annotated[int, Field(ge=0, le=10)]


class Message(BaseModel):
    role: Role
    content: str
    created_at: datetime | None = None
    # Request-only metadata, never serialized into stored/public messages.
    # Compute policy must inspect original text, not parse user-supplied tags.
    _source_content: str | None = PrivateAttr(default=None)

    @property
    def source_content(self) -> str:
        return self.content if self._source_content is None else self._source_content

    def for_provider(self, content: str) -> Message:
        projected = self.model_copy(update={"content": content})
        projected._source_content = self.source_content
        return projected

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime | None) -> datetime | None:
        # Database timestamps are UTC even when the driver drops tzinfo.
        if value is not None:
            return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
                    else value.astimezone(timezone.utc))
        return None

    reasoning_content: str | None = None
    model_name: str | None = None
    routing_reasoning_content: str | None = None
    router_model_name: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=20_000)
    generation_id: UUID | None = None
    retry_of_message_id: int | None = Field(default=None, gt=0)
    # Omit on the first turn — the server mints one and returns it.
    session_id: str | None = None
    # Override the server default for this request ("claude" | "deepseek").
    provider: Literal["claude", "deepseek", "doubao"] | None = None
    # Pin a module and skip routing entirely, e.g. "module_3".
    module: str | None = None
    # Free-form context carried from the frontend (user profile, scenario, …).
    # Available to the prompt builder; nothing here is trusted as instructions.
    metadata: dict[str, str] = Field(default_factory=dict)
    # An admin's own on/off choice for the 承接 opening. Ignored for members,
    # who follow REPLY_LEAD_ENABLED.
    reply_lead: bool | None = None


class CancelGenerationRequest(BaseModel):
    generation_id: UUID


class ChatResponse(BaseModel):
    user_created_at: datetime | None = None
    assistant_created_at: datetime | None = None
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
    achievement: Score5 | None = None
    connection: Score5 | None = None
    enjoyment: Score5 | None = None
    importance: Score5 | None = None

    note: str | None = Field(default=None, max_length=2000)

    @field_validator("time_slot", "activity")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("时间和活动内容不能为空")
        return value.strip()


class ActivityLogOut(ActivityLogIn):
    position: int


class DailySummaryIn(BaseModel):
    """The day's free-text note. Day scores live in 每日测量."""

    reflection_note: str | None = Field(default=None, max_length=4000)


class DailyMeasurementAnswers(BaseModel):
    """每日测量 answers, all required. Sleep is last night; the rest is the
    chosen day. Activity counts and minutes may be 0."""

    sleep_hours: SleepHours
    sleep_quality: SleepQuality  # 1 很好, 2 较好, 3 较差, 4 很差

    completion: Agree5  # 1 完全没有完成 … 5 全部完成
    activity_level: Agree5  # 1 几乎没有身体活动 … 5 身体活动非常多
    overall_mood: Agree5  # 1 非常糟糕 … 5 非常愉悦

    energetic: Agree5  # 1 完全不符合 … 5 非常符合
    peaceful: Agree5
    secure: Agree5
    low_mood: Agree5
    worthless: Agree5
    irritable: Agree5
    tired: Agree5
    stressed: Agree5
    lonely: Agree5

    vigorous_count: ActivityCount
    vigorous_minutes: ActivityMinutes
    moderate_count: ActivityCount
    moderate_minutes: ActivityMinutes
    light_count: ActivityCount
    light_minutes: ActivityMinutes

    @field_validator("sleep_hours")
    @classmethod
    def check_sleep_hours(cls, value: float) -> float:
        if round(value, 1) != value:
            raise ValueError("睡眠小时数最多保留 1 位小数")
        return value

    @model_validator(mode="after")
    def check_activity(self):
        for level, name in (("vigorous", "剧烈运动"), ("moderate", "中等强度运动"), ("light", "轻度运动")):
            if getattr(self, f"{level}_minutes") > 0 and getattr(self, f"{level}_count") == 0:
                raise ValueError(f"{name}填写了累计时长，次数至少为 1")
        if self.light_minutes + self.moderate_minutes + self.vigorous_minutes > 1440:
            raise ValueError("三种强度的累计时长合计不能超过一天的 1440 分钟")
        return self


class DailyMeasurementIn(BaseModel):
    """POST /api/daily-measurements — one day's 每日测量."""

    timezone: str | None = Field(default=None, max_length=64)
    local_date: date | None = None
    answers: DailyMeasurementAnswers


class DailyMeasurementUpdate(BaseModel):
    """PUT /api/daily-measurements/{id} — a correction, guarded by revision."""

    local_date: date
    expected_revision: int = Field(ge=1)
    answers: DailyMeasurementAnswers


class DailyMeasurementOut(BaseModel):
    id: int
    local_date: date
    timezone: str
    questionnaire_version: str
    revision_no: int
    submitted_at: datetime
    updated_at: datetime
    answers: DailyMeasurementAnswers


class DailyMeasurementPage(BaseModel):
    items: list[DailyMeasurementOut]
    has_more: bool


class DailyMeasurementRevisionOut(BaseModel):
    revision_no: int
    saved_at: datetime
    measurement: DailyMeasurementOut


class AssessmentSubmission(BaseModel):
    """POST /api/assessment — a completed assessment for one local day."""

    # Defaults to today in the caller's timezone. Explicit past dates support
    # backfilling; completed records cannot be dated in the future.
    local_date: date | None = None
    # IANA name, e.g. "Asia/Shanghai". Unknown values fall back to UTC.
    timezone: str | None = Field(default=None, max_length=64)

    # A record needs an activity or a note; every supplied card still has to
    # satisfy ActivityLogIn validation.
    activities: list[ActivityLogIn] = Field(..., max_length=50)
    summary: DailySummaryIn

    @model_validator(mode="after")
    def check_content(self):
        if not self.activities and not (self.summary.reflection_note or "").strip():
            raise ValueError("请至少填写一项活动，或写一点这一天的感受")
        return self


class AssessmentEditSummary(BaseModel):
    # Only legacy (version 1) records still carry these 0–10 scores.
    social_connection: int | None = Field(default=None, ge=0, le=10)
    approach_vs_avoidance: int | None = Field(default=None, ge=0, le=10)
    reflection_note: str | None = Field(default=None, max_length=4000)


class AssessmentUpdate(BaseModel):
    local_date: date
    expected_revision: int = Field(ge=1)
    activities: list[ActivityLogIn] = Field(max_length=50)
    summary: AssessmentEditSummary


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
    revision_no: int = 1
    local_date: date
    timezone: str
    status: Literal["completed", "skipped"]
    scale_version: int = 1

    social_connection: int | None = None
    approach_vs_avoidance: int | None = None
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
    "global", "module_1", "module_2", "module_3", "module_4", "router_agent", "knowledge_mediator", "reply_lead",
    "opening_message",
]


class AdminPromptItem(BaseModel):
    revision: int
    key: PromptKey
    label: str
    description: str
    content: str
    is_overridden: bool
    updated_by: str | None = None
    updated_at: datetime | None = None


class AdminPromptBundle(BaseModel):
    prompts: list[AdminPromptItem]


class AdminPromptRestore(BaseModel):
    base_revision: int = Field(..., ge=1)


class AdminPromptUpdate(AdminPromptRestore):
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


class BirthYearOnlyModel(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def reject_full_birth_date(cls, value):
        if isinstance(value, dict) and "birth_date" in value:
            raise ValueError("已改为只填写出生年份，请刷新页面后重试")
        return value


def normalize_phone(value) -> str:
    """Strip spaces and dashes; accept 1xxxxxxxxxx (optionally +86) or +<6–15 digits>."""
    raw = re.sub(r"[\s\-()]", "", value) if isinstance(value, str) else ""
    if not raw:
        raise ValueError("请填写手机号")
    mainland = re.fullmatch(r"(?:\+?86)?(1[3-9]\d{9})", raw)
    if mainland:
        return mainland.group(1)
    if re.fullmatch(r"\+\d{6,15}", raw):
        return raw
    raise ValueError("请填写有效的手机号（11 位大陆手机号，其他地区请以 + 开头）")


def normalize_research_id(value) -> str:
    """被试编号 a participant types themselves: exactly three digits, e.g. "007"."""
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        raise ValueError("请填写被试编号")
    if not re.fullmatch(r"[0-9]{3}", value):
        raise ValueError("被试编号必须是三位数字，例如 007")
    return value


class RegisterRequest(BirthYearOnlyModel):
    """POST /api/auth/register.

    Carries two kinds of field, on purpose:

    * things that do not really change (age, living situation), which are
      cheapest to ask once and never again; and
    * the two safety constraints (physical limits, movement taboos),
      which can change — but which the coach needs from its
      very first reply, so a blank profile would mean the first session runs
      without them.

    Everything here is editable afterwards through `PATCH /api/profile`. A
    healed injury or a changed preference must not be frozen at signup.
    """

    invitation_code: str = Field(..., min_length=32, max_length=64)

    @field_validator("invitation_code", mode="before")
    @classmethod
    def validate_invitation(cls, value):
        from .invitations import normalize_invitation_code
        if not isinstance(value, str):
            raise ValueError("请填写管理员提供的邀请码")
        value = normalize_invitation_code(value)
        if len(value) != 32 or any(c not in "0123456789ABCDEF" for c in value):
            raise ValueError("请填写完整有效的邀请码")
        return value

    username: str = Field(..., min_length=3, max_length=32)
    # Length is the only rule enforced. Composition rules ("one digit, one
    # symbol") measurably push people toward predictable patterns without
    # adding entropy; NIST SP 800-63B advises a length floor instead.
    password: str = Field(..., min_length=8, max_length=128)

    nickname: str = Field(..., min_length=1, max_length=58)
    # 被试编号, three digits. Stored as the account's
    # `research_participants.research_id` — the 研究编号 admins see.
    research_id: str

    @field_validator("research_id", mode="before")
    @classmethod
    def validate_research_id(cls, value):
        return normalize_research_id(value)

    # 手机号, required. Mainland numbers are 11 digits; others need a leading +.
    phone: str

    @field_validator("phone", mode="before")
    @classmethod
    def validate_phone(cls, value):
        return normalize_phone(value)

    # TINYINT UNSIGNED on the column. The upper bound is a typo guard, not a
    # claim about human lifespans.
    birth_year: int

    @field_validator("birth_year", mode="before")
    @classmethod
    def validate_birthyear(cls, value):
        from .birth_years import validate_birth_year
        return validate_birth_year(value)

    @property
    def age(self) -> int:
        from .birth_years import age_from_year
        return age_from_year(self.birth_year)
    living_status: LivingStatus | None = None
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
        return value


class Supporter(BaseModel):
    """One person the subject can realistically do something with.

    `relation` is free text, not the six-value ENUM `user_profile` uses: the
    point of the extension table is that "室友" and "教练" are real answers.
    The legacy columns mirror the first two positions; an unsupported custom
    relation projects to NULL but remains intact in the canonical list.
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
    age: int | None = None
    birth_year: int | None = None
    living_status: LivingStatus | None = None

    has_supporter: bool = False
    supporter1_relation: SupporterRelation | None = None
    supporter1_nickname: str | None = None
    supporter1_influence: SupporterInfluence | None = None
    supporter2_relation: SupporterRelation | None = None
    supporter2_nickname: str | None = None
    supporter2_influence: SupporterInfluence | None = None

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
    can_manage_models: bool = False

    # Read-only, shown for orientation. `current_module` is the programme's
    # own state machine and `risk_level` is a clinical judgement — neither is
    # something the subject sets about themselves, so `ProfileUpdate` below
    # does not accept them.
    current_module: str | None = None

    # 被试编号, from `research_participants` (not the profile tables).
    research_id: str | None = None


class ProfileUpdate(BirthYearOnlyModel):
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

    @field_validator("nickname")
    @classmethod
    def validate_nickname(cls, value: str | None) -> str:
        if value is None or not value.strip():
            raise ValueError("昵称不能为空")
        return value.strip()

    age: int | None = Field(default=None, ge=10, le=120)
    birth_year: int | None = None

    @field_validator("birth_year", mode="before")
    @classmethod
    def validate_birthyear(cls, value):
        from .birth_years import validate_birth_year
        return validate_birth_year(value)
    living_status: LivingStatus | None = None

    has_supporter: bool | None = None
    supporter1_relation: SupporterRelation | None = None
    supporter1_nickname: str | None = Field(default=None, max_length=64)
    supporter1_influence: SupporterInfluence | None = None
    supporter2_relation: SupporterRelation | None = None
    supporter2_nickname: str | None = Field(default=None, max_length=64)
    supporter2_influence: SupporterInfluence | None = None

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

    # 被试编号. Written to `research_participants`, not the profile tables, and
    # cannot be cleared here (only an administrator can).
    research_id: str | None = None

    @field_validator("research_id", mode="before")
    @classmethod
    def validate_research_id(cls, value):
        return normalize_research_id(value)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class BirthYearRequest(BirthYearOnlyModel):
    birth_year: int

    @field_validator("birth_year", mode="before")
    @classmethod
    def validate_birthyear(cls, value):
        from .birth_years import validate_birth_year
        return validate_birth_year(value)


class ChangePasswordRequest(BaseModel):
    """POST /api/auth/password — change your own password while signed in.

    The current password is required even though the caller already holds a
    valid token. A token can be lying around on a borrowed or unlocked device;
    demanding the password proves the person at the keyboard is the account
    holder, not just whoever the browser was left signed in as.
    """

    current_password: str = Field(..., min_length=1, max_length=128)
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
    # `user_profile.uuid`. The frontend does not need it, but it is what every
    # clinical table keys on, so surfacing it makes support questions ("which
    # row is mine?") answerable without a database session.
    profile_uuid: str
    current_module: str | None = None
    role: Literal["user", "admin"] = "user"
    email: EmailStr | None = None
    email_verified: bool = False
    email_required: bool = False
    birth_year_required: bool = False
    email_delivery_available: bool = False


class AdminAccountItem(BaseModel):
    id: int
    username: str
    nickname: str | None = None
    role: Literal["user", "admin"]
    created_at: datetime
    last_login_at: datetime | None = None
    research_id: str | None = None
    phone: str | None = None


class AdminResearchIdUpdate(BaseModel):
    # Letters, digits, "-" and "_" only, so the ID is safe in CSV and file names.
    research_id: str | None = Field(default=None, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")


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


class ConversationCreate(BaseModel):
    """A mode is selected once, at creation; chat metadata cannot change it."""

    model_config = ConfigDict(extra="forbid")
    routing_mode: Literal["router_code", "router_only"] = "router_code"
    reply_mode: Literal["ack_deep"] = "ack_deep"  # the only mode; kept so older clients still validate
    reply_effort: Literal["low", "high", "max"] = "low"


class ConversationSummary(BaseModel):
    """One row in the sidebar's conversation list."""

    session_id: str
    title: str
    updated_at: datetime
    pinned: bool = False


class MessageTiming(BaseModel):
    # Observed stream interval, not a provider's internal compute-time claim.
    reply_thinking_ms: int | None = None
    reply_generation_ms: int | None = None
    router_processing_ms: int | None = None


class ConversationMessageDetail(Message):
    id: int | None = None
    reply_to_message_id: int | None = None
    timing: MessageTiming | None = None
    reply_status: Literal["interrupted", "incomplete"] | None = None


class ConversationThinkingUpdate(BaseModel):
    enabled: bool = Field(strict=True)


class ConversationMediatorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


class ConversationReplyEffortUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effort: Literal["low", "high", "max"]


class ConversationThinkingUpdate(BaseModel):
    enabled: bool = Field(strict=True)


class ConversationDetail(ConversationSummary):
    """A conversation's full transcript, for switching into it."""

    revision: int = 0
    thinking_enabled: bool | None = None
    knowledge_mediator_enabled: bool = True  # historical architecture selector
    knowledge_mediator_node_enabled: bool = False
    knowledge_mode: Literal["legacy_mediator", "astrbot_hybrid"] = "legacy_mediator"
    reply_mode: Literal["ack_deep"] = "ack_deep"
    reply_effort: Literal["low", "high", "max"] | None = None
    reply_effort_options: list[Literal["low", "high", "max"]] = Field(default_factory=list)
    messages: list[ConversationMessageDetail] = Field(default_factory=list)
    # Durable pointer for the next turn. This is deliberately not called
    # `module`: the latest assistant reply may belong to the previous module.
    next_module: str | None = None
    routing_mode: Literal["router_code", "router_only"] = "router_code"


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
