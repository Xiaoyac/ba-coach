import { API_BASE } from "@/lib/api";
import { apiHeaders } from "@/lib/http";

export type PromptKey =
  | "global"
  | "module_1"
  | "module_2"
  | "module_3"
  | "module_4"
  | "router_agent"
  | "reply_lead"
  | "knowledge_mediator"
  | "opening_message";

export interface AdminPromptItem {
  revision: number;
  key: PromptKey;
  label: string;
  description: string;
  content: string;
  is_overridden: boolean;
  updated_by: string | null;
  updated_at: string | null;
}

async function parsePrompt(res: Response): Promise<AdminPromptItem> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(res.status === 409 ? "提示词已被其他管理员或系统更新。草稿已保留，请载入最新版本并核对后再保存。" : typeof body.detail === "string" ? body.detail : `提示词操作失败（${res.status}），请刷新页面后重试。`);
  }
  return res.json() as Promise<AdminPromptItem>;
}

export async function fetchAdminPrompts(
  signal?: AbortSignal,
): Promise<AdminPromptItem[]> {
  const res = await fetch(`${API_BASE}/api/admin/prompts`, {
    headers: apiHeaders(),
    signal,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`读取提示词失败（${res.status}）：${detail}`);
  }
  const body = (await res.json()) as { prompts: AdminPromptItem[] };
  return body.prompts;
}

export async function saveAdminPrompt(
  key: PromptKey,
  content: string,
  baseRevision: number,
): Promise<AdminPromptItem> {
  const res = await fetch(
    `${API_BASE}/api/admin/prompts/${encodeURIComponent(key)}`,
    {
      method: "PUT",
      headers: apiHeaders({ json: true }),
      body: JSON.stringify({ content, base_revision: baseRevision }),
    },
  );
  return parsePrompt(res);
}

export async function resetAdminPrompt(
  key: PromptKey,
  baseRevision: number,
): Promise<AdminPromptItem> {
  const res = await fetch(
    `${API_BASE}/api/admin/prompts/${encodeURIComponent(key)}?base_revision=${baseRevision}`,
    { method: "DELETE", headers: apiHeaders() },
  );
  return parsePrompt(res);
}

export interface PromptVersion {
  revision: number; actor: string; action: string; created_at: string;
  origin: "admin" | "system"; is_overridden: boolean; restored_from: number | null;
}
export interface PromptComparison {
  before: PromptVersion; after: PromptVersion;
  changes: { kind: "equal" | "insert" | "delete"; text: string }[];
}
async function historyRequest<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}/api/admin/prompts/${path}`, {headers: apiHeaders(), signal});
  if (!res.ok) throw new Error(`读取版本失败（${res.status}），请重试。`);
  return res.json() as Promise<T>;
}
export const fetchPromptHistory = (key: PromptKey, before?: number, signal?: AbortSignal) =>
  historyRequest<{items: PromptVersion[]; next_before: number | null}>(`${key}/history${before ? `?before=${before}` : ""}`, signal);
export const comparePromptVersions = (key: PromptKey, before: number, after: number, signal?: AbortSignal) =>
  historyRequest<PromptComparison>(`${key}/compare?from_revision=${before}&to_revision=${after}`, signal);
export const fetchPromptVersion = (key: PromptKey, revision: number, signal?: AbortSignal) =>
  historyRequest<PromptVersion & {content: string}>(`${key}/history/${revision}`, signal);
export async function restorePromptVersion(key: PromptKey, revision: number, baseRevision: number) {
  const res = await fetch(`${API_BASE}/api/admin/prompts/${key}/history/${revision}/restore`, {
    method: "POST", headers: apiHeaders({json:true}), body: JSON.stringify({base_revision:baseRevision}),
  });
  return parsePrompt(res);
}
