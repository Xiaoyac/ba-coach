/**
 * Client for 每日测量 (/api/daily-measurements), the study's daily
 * questionnaire. Separate from 每日记录 (`@/lib/assessment`): its own records,
 * its own one-per-day rule and its own revision history.
 */

import { API_BASE } from "@/lib/api";
import { localTimezone } from "@/lib/identity";
import { apiHeaders, checkAuthentication } from "@/lib/http";

export interface MeasurementAnswers {
  /** Last night, 0–24 in steps of 0.1. */
  sleep_hours: number;
  /** 1 很好, 2 较好, 3 较差, 4 很差. */
  sleep_quality: number;
  /** Q7–Q9, 1–5 from the lowest to the highest option. */
  completion: number;
  activity_level: number;
  overall_mood: number;
  /** Q10, 1 完全不符合 … 5 非常符合. */
  energetic: number;
  peaceful: number;
  secure: number;
  low_mood: number;
  worthless: number;
  irritable: number;
  tired: number;
  stressed: number;
  lonely: number;
  /** Q11: occurrences and total minutes per intensity; 0 is an answer. */
  vigorous_count: number;
  vigorous_minutes: number;
  moderate_count: number;
  moderate_minutes: number;
  light_count: number;
  light_minutes: number;
}

export interface MeasurementRecord {
  id: number;
  local_date: string;
  timezone: string;
  questionnaire_version: string;
  revision_no: number;
  submitted_at: string;
  updated_at: string;
  answers: MeasurementAnswers;
}

export class MeasurementRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function parse<T>(res: Response): Promise<T> {
  checkAuthentication(res);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail
      : Array.isArray(body?.detail) && typeof body.detail[0]?.msg === "string" ? body.detail[0].msg.replace(/^Value error, /, "")
      : `每日测量暂时无法加载或保存，请稍后重试（${res.status}）。`;
    throw new MeasurementRequestError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

const BASE = `${API_BASE}/api/daily-measurements`;

export async function submitMeasurement(localDate: string, answers: MeasurementAnswers): Promise<MeasurementRecord> {
  return parse(await fetch(BASE, {
    method: "POST", headers: apiHeaders({ json: true }),
    body: JSON.stringify({ timezone: localTimezone(), local_date: localDate, answers }),
  }));
}

export async function updateMeasurement(id: number, payload: {
  local_date: string; expected_revision: number; answers: MeasurementAnswers;
}): Promise<MeasurementRecord> {
  return parse(await fetch(`${BASE}/${id}`, {
    method: "PUT", headers: apiHeaders({ json: true }), body: JSON.stringify(payload),
  }));
}

export async function fetchMeasurementByDate(date: string, signal?: AbortSignal): Promise<MeasurementRecord | null> {
  return parse(await fetch(`${BASE}/by-date?local_date=${encodeURIComponent(date)}`, {
    headers: apiHeaders(), cache: "no-store", signal,
  }));
}

export async function fetchMeasurementHistory(offset = 0, limit = 10): Promise<{ items: MeasurementRecord[]; has_more: boolean }> {
  return parse(await fetch(`${BASE}/history?limit=${limit}&offset=${offset}`, {
    headers: apiHeaders(), cache: "no-store",
  }));
}
