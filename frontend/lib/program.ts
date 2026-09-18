import { API_BASE } from "@/lib/api";
import { apiHeaders, checkAuthentication } from "@/lib/http";

export type GoalSelection = { goal_id: string; resume?: boolean };
export type GoalKind = "primary" | "secondary" | "unclassified";
export type ScheduleKind = "recurring" | "one_off" | "unspecified";
export type ActivityEventKind = "performed" | "not_performed" | "idea";

export type ActivityRecord = {
  id: string;
  activity_content: string;
  event_kind: ActivityEventKind;
  occurred_at_text: string | null;
  effect: string | null;
  goal_id: string | null;
};

export type GoalPlan = {
  activity_content?: string | null;
  schedule_text?: string | null;
  location?: string | null;
  duration_minutes?: number | null;
  schedule_kind?: ScheduleKind | null;
  review_cadence?: string | null;
};

export type ProgramGoal = {
  id: string;
  title: string;
  status: string;
  goal_kind?: GoalKind | null;
  long_term_direction?: string | null;
  created_at?: string;
  updated_at?: string;
  latest_cycle?: { ordinal: number; status: string } | null;
  plan?: GoalPlan | null;
};

export type GoalContext = {
  goal_kind: GoalKind;
  long_term_direction: string | null;
} | null;

export type ArchivedPlan = GoalPlan & {
  id: string; version_no: number; record_status: string; confirmation_status: string;
  companion?: string | null; core_values?: unknown; core_values_impact?: string | null;
  potential_barriers?: unknown; barrier_coping_plan?: unknown; difficulty?: string | null;
  resources?: unknown; created_at: string; updated_at: string;
};
export type ArchivedCycle = {
  id: string; ordinal: number; status: string; plan_version: number | null;
  activity_content: string | null; schedule_text: string | null;
  started_at: string | null; completed_at: string | null; created_at: string;
  review_status: string | null; review_summary: string | null; review_action: string | null;
};
export type ArchivedActivity = ActivityRecord & { status: string; created_at: string; cycle_ordinal: number | null };
export type GoalHistory = {
  goal: ProgramGoal; plans: ArchivedPlan[]; cycles: ArchivedCycle[]; activities: ArchivedActivity[];
  totals: { plans: number; cycles: number; activities: number }; page: number; page_size: number;
};

export async function fetchGoalHistory(goalId: string, page: number, signal?: AbortSignal): Promise<GoalHistory> {
  const response = await fetch(`${API_BASE}/api/program/goals/${encodeURIComponent(goalId)}/history?page=${page}&page_size=12`, {
    headers: apiHeaders(), cache: "no-store", signal,
  });
  checkAuthentication(response);
  if (!response.ok) throw new Error(response.status === 404 ? "这个目标已不存在或无法访问。" : "目标历史暂时无法加载，请重试。");
  return response.json();
}

export type ProgramState = {
  enabled: boolean;
  m1_reusable?: boolean;
  runtime?: { active_goal_id: string | null; row_version: number };
  goals?: ProgramGoal[];
  goal_context?: GoalContext;
  activity_records?: ActivityRecord[];
};

export const goalKindLabel: Record<GoalKind, string> = {
  primary: "主目标",
  secondary: "次要目标",
  unclassified: "待归类",
};

export const eventKindLabel: Record<ActivityEventKind, string> = {
  performed: "实际完成",
  not_performed: "未能完成",
  idea: "想法 / 备选",
};

export const scheduleKindLabel: Record<ScheduleKind, string> = {
  recurring: "规律安排",
  one_off: "单次安排",
  unspecified: "安排待明确",
};

export function activityEffect(record: ActivityRecord): string | null {
  return record.effect?.trim() || record.occurred_at_text?.trim() || null;
}

async function parse(response: Response): Promise<ProgramState> {
  checkAuthentication(response);
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "目标操作失败，请刷新后重试");
  return result;
}

export async function fetchProgram(sessionId: string): Promise<ProgramState> {
  return parse(await fetch(`${API_BASE}/api/program/${encodeURIComponent(sessionId)}`, {
    headers: apiHeaders(), cache: "no-store", signal: AbortSignal.timeout(15000),
  }));
}

export async function chooseProgramGoal(sessionId: string, selection: GoalSelection, rowVersion: number): Promise<ProgramState> {
  return parse(await fetch(`${API_BASE}/api/program/${encodeURIComponent(sessionId)}/goal`, {
    method: "POST", headers: apiHeaders({ json: true }),
    body: JSON.stringify({ ...selection, row_version: rowVersion }), signal: AbortSignal.timeout(30000),
  }));
}
