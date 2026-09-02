import { API_BASE, type ChatMessage } from "@/lib/api";
import { apiHeaders } from "@/lib/http";

export type SandboxModule = "module_1" | "module_2" | "module_3" | "module_4";

export interface AdminSandboxConversation {
  session_id: string;
  title: string;
  updated_at: string;
  pinned: boolean;
  messages: ChatMessage[];
  next_module: SandboxModule;
}

export async function startAdminSandbox(
  module: SandboxModule,
): Promise<AdminSandboxConversation> {
  const res = await fetch(`${API_BASE}/api/admin/sandbox/module`, {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify({ module }),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`启动模块沙盒失败（${res.status}）：${detail}`);
  }
  return res.json() as Promise<AdminSandboxConversation>;
}
