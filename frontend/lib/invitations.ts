import { API_BASE } from "@/lib/api";
import { apiHeaders } from "@/lib/http";

export interface Invitation {
  id: number;
  code: string;
  created_at: string;
  used_at: string | null;
  used_by_username: string | null;
}
export interface InvitationList { invitations: Invitation[]; has_more: boolean }

async function result(response: Response): Promise<InvitationList> {
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "邀请码操作失败，请稍后重试");
  return body;
}
export async function listInvitations(used: boolean, offset: number, signal?: AbortSignal) {
  return result(await fetch(`${API_BASE}/api/admin/invitations?used=${used}&offset=${offset}`, {
    headers: apiHeaders(), cache: "no-store", signal,
  }));
}
export async function createInvitations(count: number) {
  return result(await fetch(`${API_BASE}/api/admin/invitations`, {
    method: "POST", headers: apiHeaders({ json: true }), body: JSON.stringify({ count }),
  }));
}
