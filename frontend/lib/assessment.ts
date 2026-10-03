/**
 * Client for the Daily Behavioral Activation Assessment.
 *
 * Identity (subject id, timezone) lives in `@/lib/identity` — it is shared
 * with the chat and conversation-sidebar clients, not specific to this file.
 */

import { API_BASE } from "@/lib/api";
import { localTimezone } from "@/lib/identity";
import { apiHeaders, checkAuthentication } from "@/lib/http";

export type Score = number;

export interface ActivityLog {
  time_slot: string;
  activity: string;
  emotion: Score | null;
  achievement: Score | null;
  connection: Score | null;
  enjoyment: Score | null;
  importance: Score | null;
  note: string | null;
}

export interface DailySummary {
  social_connection?: Score | null;
  approach_vs_avoidance?: Score | null;
  completion_rate: Score | null;
  completion_not_applicable: boolean;
  activity_level: Score | null;
  overall_mood: Score | null;
  reflection_note: string | null;
}

export interface StoredActivityLog extends ActivityLog {
  position: number;
}

export interface AssessmentRecord {
  scale_version?: number;
  revision_no?: number;
  completion_not_applicable?: boolean;
  id: number;
  local_date: string;
  timezone: string;
  status: "completed" | "skipped";
  completion_rate: Score | null;
  activity_level: Score | null;
  social_connection: Score | null;
  approach_vs_avoidance: Score | null;
  overall_mood: Score | null;
  reflection_note: string | null;
  activities: StoredActivityLog[];
}

export interface AssessmentHistoryPage {
  items: AssessmentRecord[];
  has_more: boolean;
  next_offset: number | null;
}

export class AssessmentRequestError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

export function isRetryableAssessmentRead(error: unknown): boolean {
  return error instanceof TypeError
    || (error instanceof DOMException && error.name === "TimeoutError")
    || (error instanceof AssessmentRequestError && [408, 429, 500, 502, 503, 504].includes(error.status));
}

async function parse<T>(res: Response, what: string): Promise<T> {
  checkAuthentication(res);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new AssessmentRequestError(typeof body?.detail === "string" ? body.detail : `每日记录暂时无法加载或保存，请稍后重试（${res.status}）。`, res.status);
  }
  return res.json() as Promise<T>;
}

export async function submitAssessment(payload: {
  local_date?: string;
  activities: ActivityLog[];
  summary: DailySummary;
}): Promise<AssessmentRecord> {
  const res = await fetch(`${API_BASE}/api/assessment`, {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify({ timezone: localTimezone(), ...payload }),
  });
  return parse<AssessmentRecord>(res, "Saving the assessment");
}

export async function fetchAssessmentHistory(
  offset = 0,
  limit = 10,
  signal?: AbortSignal,
): Promise<AssessmentHistoryPage> {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  const res = await fetch(`${API_BASE}/api/assessment/history?${params}`, {
    headers: apiHeaders(),
    signal,
  });
  return parse(res, "Loading assessment history");
}


export async function updateAssessment(id: number, payload: {
  local_date: string; expected_revision: number; activities: ActivityLog[]; summary: DailySummary;
}): Promise<AssessmentRecord> {
  return parse(await fetch(`${API_BASE}/api/assessment/${id}`, {
    method: "PUT", headers: apiHeaders({ json: true }), body: JSON.stringify(payload),
  }), "Updating assessment");
}

export async function fetchAssessmentByDate(date: string, signal?: AbortSignal): Promise<AssessmentRecord | null> {
  return parse(await fetch(`${API_BASE}/api/assessment/by-date?local_date=${encodeURIComponent(date)}`, {
    headers: apiHeaders(), cache: "no-store", signal,
  }), "Loading selected date");
}
