import { API_BASE } from "@/lib/api";
import { apiHeaders } from "@/lib/http";

export type PromptKey =
  | "global"
  | "module_1"
  | "module_2"
  | "module_3"
  | "module_4"
  | "router_agent";

export interface AdminPromptItem {
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
    const detail = await res.text();
    throw new Error(`提示词操作失败（${res.status}）：${detail}`);
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
): Promise<AdminPromptItem> {
  const res = await fetch(
    `${API_BASE}/api/admin/prompts/${encodeURIComponent(key)}`,
    {
      method: "PUT",
      headers: apiHeaders({ json: true }),
      body: JSON.stringify({ content }),
    },
  );
  return parsePrompt(res);
}

export async function resetAdminPrompt(
  key: PromptKey,
): Promise<AdminPromptItem> {
  const res = await fetch(
    `${API_BASE}/api/admin/prompts/${encodeURIComponent(key)}`,
    { method: "DELETE", headers: apiHeaders() },
  );
  return parsePrompt(res);
}
