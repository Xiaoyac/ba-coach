import { API_BASE } from './api';
import { apiHeaders, checkAuthentication } from './http';
import type { AssessmentRecord } from './assessment';

export type RecordFilters = { query: string; start: string; end: string; scale_version: string };
export type AdminRecord = { subject_id: string; username: string | null; display_id: string | null; record: AssessmentRecord };
export type AdminRecordPage = { items: AdminRecord[]; total: number; users: number; versions: Record<string, number>; has_more: boolean };

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
export async function exportAdminRecords(filters: RecordFilters, kind: 'daily' | 'activities', signal: AbortSignal) {
  const query=params(filters); query.set('kind',kind);
  const response=await checked(await fetch(`${API_BASE}/api/admin/assessments/export.csv?${query}`,{headers:apiHeaders(),signal,cache:'no-store'}));
  const blob=await response.blob();
  if(signal.aborted) return;
  const url=URL.createObjectURL(blob), a=document.createElement('a');
  a.href=url; a.download=`daily-records-${kind}.csv`; document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(()=>URL.revokeObjectURL(url),1000);
}
