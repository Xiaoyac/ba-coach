import { API_BASE } from "@/lib/api";
import { apiHeaders, checkAuthentication } from "@/lib/http";

export type GoalCardFields = {
  activity_content?: string | null;
  schedule_text?: string | null;
  location?: string | null;
  duration_minutes?: number | null;
  frequency_text?: string | null;
  difficulty_rating?: number | null;
  potential_barriers?: string | null;
  barrier_coping_plan?: string | null;
  long_term_direction?: string | null;
};

export type FormulationCard = {
  id: string;
  kind: "primary" | "secondary";
  phase: "formulating" | "discussing" | "ready" | "confirmed" | "paused";
  revision: number;
  fields: GoalCardFields;
  concerns: string[];
  goal_id?: string | null;
  plan_id?: string | null;
  confirmed_at?: string | null;
  updated_at?: string | null;
};

type CardResponse = { enabled: boolean; card: FormulationCard | null };
type SubmissionResponse = CardResponse & { submission_text: string };

async function read<T>(response: Response): Promise<T> {
  checkAuthentication(response);
  const result = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(response.status === 409
      ? "目标卡已有更新。你的填写仍保留，请查看最新卡片后再提交。"
      : typeof result?.detail === "string" ? result.detail : "目标卡暂时无法读取，请稍后重试。");
  }
  return result as T;
}

export function fetchGoalCard(sessionId: string, signal?: AbortSignal): Promise<CardResponse> {
  return fetch(`${API_BASE}/api/program/${encodeURIComponent(sessionId)}/goal-card`, {
    headers: apiHeaders(), cache: "no-store", signal,
  }).then(read<CardResponse>);
}

export function submitGoalCard(sessionId: string, card: FormulationCard, fields: GoalCardFields, signal?: AbortSignal): Promise<SubmissionResponse> {
  return fetch(`${API_BASE}/api/program/${encodeURIComponent(sessionId)}/goal-card`, {
    method: "PUT", headers: apiHeaders({ json: true }), signal,
    body: JSON.stringify({ card_id: card.id, revision: card.revision, fields }),
  }).then(read<SubmissionResponse>);
}

export function goalCardKind(kind: FormulationCard["kind"]): string {
  return kind === "primary" ? "核心目标" : "次要目标";
}

export const goalCardFieldLabels: Record<keyof GoalCardFields, string> = {
  activity_content: "想做的活动", schedule_text: "什么时候做", location: "在哪里做",
  duration_minutes: "每次多长时间", frequency_text: "大约多常做", difficulty_rating: "你觉得有多难",
  potential_barriers: "可能遇到的困难", barrier_coping_plan: "可以怎么应对", long_term_direction: "这对你有什么意义",
};

export function goalCardFieldValue(key: keyof GoalCardFields, value: GoalCardFields[keyof GoalCardFields]): string {
  if (value === null || value === undefined || value === "") return "还没想好";
  if (key === "duration_minutes") return `${value} 分钟`;
  if (key === "difficulty_rating") return `${value} / 10`;
  return String(value);
}
