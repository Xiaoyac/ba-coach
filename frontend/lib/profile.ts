/**
 * The subject's editable profile.
 *
 * The option lists below mirror the ENUM/SET definitions on `user_profile`
 * (and the Literals in the backend's `schemas.py`). They are duplicated here
 * rather than fetched because they are part of the database schema, not
 * configuration — a value that is not in the column's ENUM is a 422 no matter
 * what the frontend offers, so the honest thing is to only offer valid ones.
 * If the column ever changes, both sides change together.
 */

import { apiHeaders, checkAuthentication } from "@/lib/http";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export const LIVING_STATUS = ["独居", "和家人", "和朋友", "和恋人"] as const;
export const SUPPORTER_RELATION = [
  "父母", "恋人", "子女", "朋友", "兄弟姐妹", "同事",
] as const;
export const SUPPORTER_INFLUENCE = ["弱", "中", "强"] as const;
export const COMMUNICATION_PREFERENCE = [
  "直接明了", "温柔引导", "理性分析", "轻松幽默",
] as const;
export const REMINDER_FREQUENCY = [
  "每天一次", "隔天一次", "每三天一次", "每周一次",
  "仅在我主动找你时提醒", "暂时不需要提醒",
] as const;
export const REMINDER_TIME_SLOT = [
  "早晨7-9", "上午9-12", "中午12-14", "下午14-18", "傍晚18-21", "晚上21-23",
] as const;
export const PHYSICAL_CONDITION = [
  "膝关节损伤", "腰背酸痛", "慢性疼痛", "易疲劳", "睡眠障碍",
  "偏头痛", "哮喘", "眩晕", "鼻炎", "术后恢复期",
] as const;
export const BEHAVIOR_TABOO = [
  "不能剧烈运动", "不能久站", "不能晒太阳", "怕吵闹", "怕人多",
  "怕拥挤闭塞的地方", "不坐公共交通",
] as const;
export const CONTENT_TABOO = [
  "不谈工作", "不谈学习", "不谈家庭", "不谈身材外貌", "不谈感情",
  "不谈未来计划", "不喜欢被比较", "反感正能量说教", "不喜欢被经常催促",
] as const;
export const ACTIVITY_ENVIRONMENT = ["室内", "户外", "都可以"] as const;
export const ACTIVITY_SOCIAL = ["独自", "一对一", "群体", "都可以"] as const;
export const ACTIVITY_INTENSITY = ["安静", "热闹", "都可以"] as const;
export type ModelProvider = "deepseek" | "doubao";

export interface Supporter {
  /** Free text — not the six-value ENUM `user_profile` uses. */
  relation: string;
  nickname: string | null;
  influence: string | null;
}

/** Minutes from midnight, so "19:30" is 1170. */
export interface ReminderWindow {
  start_minute: number;
  end_minute: number;
}

export function minutesToHHMM(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function hhmmToMinutes(value: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value);
  if (!match) return null;
  const h = Number(match[1]);
  const m = Number(match[2]);
  if (h > 23 || m > 59) return null;
  return h * 60 + m;
}

export interface Profile {
  nickname: string | null;
  tag: string | null;
  display_id: string | null;
  age: number | null;
  living_status: string | null;

  has_supporter: boolean;
  supporter1_relation: string | null;
  supporter1_nickname: string | null;
  supporter1_influence: string | null;
  supporter2_relation: string | null;
  supporter2_nickname: string | null;
  supporter2_influence: string | null;

  communication_preference: string | null;
  reminder_frequency: string | null;
  reminder_time_slot: string | null;

  physical_condition: string[];
  behavior_taboo: string[];
  content_taboo: string[];

  activity_environment: string | null;
  activity_social: string | null;
  activity_intensity: string | null;

  /**
   * From the app-owned extension table. `supporter1_*`/`supporter2_*` and
   * `reminder_time_slot` above are the lossy projection of these onto the
   * externally-owned `user_profile`, kept in sync by the server — read these,
   * not those.
   */
  supporters: Supporter[];
  reminder_window: ReminderWindow | null;

  /** Temporary product setting: which configured model answers new turns. */
  preferred_provider: ModelProvider;
  available_providers: Record<ModelProvider, boolean>;

  /** Read-only — programme state, not something the subject sets. */
  current_module: string | null;
}

/**
 * A partial edit. Omitted keys are left untouched by the server; an explicit
 * `null` clears that field. That distinction is the whole reason this is a
 * PATCH and not a PUT — sending the full object back would let a stale form
 * silently revert a change made on another device.
 */
export type ProfilePatch = Partial<
  Omit<Profile, "current_module" | "available_providers" | "display_id">
>;

async function parse<T>(res: Response, what: string): Promise<T> {
  checkAuthentication(res);
  if (res.status >= 500) throw new Error("档案服务暂时不可用，请稍后重新加载。");
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new Error(detail ?? `${what}失败（${res.status}）`);
  }
  return res.json() as Promise<T>;
}

export async function fetchProfile(signal?: AbortSignal): Promise<Profile> {
  const res = await fetch(`${API_BASE}/api/profile`, {
    headers: apiHeaders(),
    // Provider availability is runtime state: it can change whenever the
    // backend is restarted with a new API key/model. Reusing an older GET
    // response leaves a newly configured provider incorrectly disabled.
    cache: "no-store",
    signal,
  });
  return parse(res, "读取档案");
}

export async function updateProfile(patch: ProfilePatch): Promise<Profile> {
  const res = await fetch(`${API_BASE}/api/profile`, {
    method: "PATCH",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify(patch),
  });
  return parse(res, "保存档案");
}
