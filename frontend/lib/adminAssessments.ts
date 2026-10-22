import { API_BASE } from './api';
import { apiHeaders, checkAuthentication } from './http';
import type { AssessmentRecord } from './assessment';
import type { MeasurementRecord } from './dailyMeasurement';

export type RecordFilters = { query: string; start: string; end: string; scale_version: string };
export type AdminRecord = { subject_id: string; username: string | null; nickname: string | null; research_id?: string | null; record: AssessmentRecord };
export type AdminRecordPage = { items: AdminRecord[]; total: number; users: number; versions: Record<string, number>; has_more: boolean };
export type AdminRecordUser = {
  subject_id: string;
  username: string | null;
  nickname: string | null;
  today_completed: boolean;
  record_count: number;
  date_count: number;
  last_record_date: string | null;
};
export type AdminRecordUserPage = {
  items: AdminRecordUser[];
  total: number;
  has_more: boolean;
  today: string;
  time_zone: 'Asia/Shanghai';
};
export type AdminRecordDates = {
  subject_id: string;
  items: { local_date: string; record_count: number; activity_count: number }[];
  total_dates: number;
};
export type AdminRecordDay = { subject_id: string; local_date: string; items: AdminRecord[]; total: number };

function params(filters: RecordFilters) {
  return new URLSearchParams(Object.entries(filters).filter(([,v]) => v.trim()));
}
async function checked(response: Response) {
  checkAuthentication(response);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : `每日记录读取失败（${response.status}），请重试`);
  }
  return response;
}
export async function fetchAdminRecords(filters: RecordFilters, offset: number, signal: AbortSignal): Promise<AdminRecordPage> {
  const query=params(filters); query.set('offset',String(offset)); query.set('limit','20');
  const response = await checked(await fetch(`${API_BASE}/api/admin/assessments?${query}`, {headers:apiHeaders(),signal,cache:'no-store'}));
  return response.json();
}
export async function fetchAdminRecordUsers(query: string, offset: number, signal: AbortSignal): Promise<AdminRecordUserPage> {
  const search = new URLSearchParams({ query, offset: String(offset), limit: '20' });
  const response = await checked(await fetch(`${API_BASE}/api/admin/assessments/users?${search}`, {
    headers: apiHeaders(), signal, cache: 'no-store',
  }));
  return response.json();
}
export async function fetchAdminRecordDates(subjectId: string, signal: AbortSignal): Promise<AdminRecordDates> {
  const response = await checked(await fetch(`${API_BASE}/api/admin/assessments/users/${encodeURIComponent(subjectId)}/dates`, {
    headers: apiHeaders(), signal, cache: 'no-store',
  }));
  return response.json();
}
export async function fetchAdminRecordDay(subjectId: string, localDate: string, signal: AbortSignal): Promise<AdminRecordDay> {
  const response = await checked(await fetch(`${API_BASE}/api/admin/assessments/users/${encodeURIComponent(subjectId)}/days/${encodeURIComponent(localDate)}`, {
    headers: apiHeaders(), signal, cache: 'no-store',
  }));
  return response.json();
}
export type ExportKind = 'daily' | 'activities' | 'measurements';
export async function exportAdminRecords(filters: RecordFilters, kind: ExportKind, signal: AbortSignal) {
  const query=params(filters);
  // 每日测量 is its own questionnaire with its own endpoint; it has no scale versions.
  if (kind === 'measurements') query.delete('scale_version'); else query.set('kind',kind);
  const path = kind === 'measurements' ? 'measurements.csv' : 'export.csv';
  const response=await checked(await fetch(`${API_BASE}/api/admin/assessments/${path}?${query}`,{headers:apiHeaders(),signal,cache:'no-store'}));
  const blob=await response.blob();
  if(signal.aborted) return;
  const url=URL.createObjectURL(blob), a=document.createElement('a');
  a.href=url; a.download=kind === 'measurements' ? 'daily-measurements.csv' : `daily-records-${kind}.csv`; document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(()=>URL.revokeObjectURL(url),1000);
}

// 每日测量 per user. Read-only; separate from 每日记录 above.
export type AdminMeasurementUser = {
  subject_id: string;
  username: string | null;
  nickname: string | null;
  research_id: string | null;
  today_completed: boolean;
  day_count: number;
  last_date: string | null;
};
export type AdminMeasurementUserPage = {
  items: AdminMeasurementUser[];
  total: number;
  today_count: number;
  has_more: boolean;
  today: string;
  time_zone: 'Asia/Shanghai';
};
export type AdminUserMeasurements = { subject_id: string; items: MeasurementRecord[]; total: number };

export async function fetchAdminMeasurementUsers(query: string, offset: number, signal: AbortSignal): Promise<AdminMeasurementUserPage> {
  const search = new URLSearchParams({ query, offset: String(offset), limit: '20' });
  const response = await checked(await fetch(`${API_BASE}/api/admin/assessments/measurements/users?${search}`, {
    headers: apiHeaders(), signal, cache: 'no-store',
  }));
  return response.json();
}
export async function fetchAdminUserMeasurements(subjectId: string, signal: AbortSignal): Promise<AdminUserMeasurements> {
  const response = await checked(await fetch(`${API_BASE}/api/admin/assessments/measurements/users/${encodeURIComponent(subjectId)}`, {
    headers: apiHeaders(), signal, cache: 'no-store',
  }));
  return response.json();
}
