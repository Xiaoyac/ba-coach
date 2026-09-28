import { API_BASE, type ChatMessage } from "@/lib/api";
import type { KnowledgeReferences } from "@/lib/conversations";
import { apiHeaders, checkAuthentication } from "@/lib/http";

export interface CreatedConversationShare {
  id: string;
  title: string;
  created_at: string;
  message_count: number;
  snapshot_version: number;
  token: string;
  path: string;
}

export interface SharedConversation {
  snapshot_version: number;
  title: string;
  created_at: string;
  messages: (ChatMessage & { knowledge_references: KnowledgeReferences | null })[];
}

export class ShareRequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ShareRequestError";
  }
}

async function parse<T>(response: Response, authenticated: boolean): Promise<T> {
  if (authenticated) checkAuthentication(response);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message = response.status === 404 && !authenticated
      ? "分享链接不存在或已失效。"
      : typeof body?.detail === "string" ? body.detail : "分享请求失败，请稍后重试。";
    throw new ShareRequestError(message, response.status);
  }
  return response.json() as Promise<T>;
}

const ownerPath = (sessionId: string) => `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/shares`;

export async function createConversationShare(sessionId: string): Promise<CreatedConversationShare> {
  const response = await fetch(ownerPath(sessionId), { method: "POST", headers: apiHeaders(), cache: "no-store" });
  return parse(response, true);
}

export async function fetchSharedConversation(token: string, signal?: AbortSignal): Promise<SharedConversation> {
  // The token authorizes this snapshot only. Never forward the viewer's login
  // credential, and never fetch details through private conversation APIs.
  const response = await fetch(`${API_BASE}/api/shares/${encodeURIComponent(token)}`, {
    signal, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
    headers: { "ngrok-skip-browser-warning": "true" },
  });
  return parse(response, false);
}

export function shareUrl(token: string, origin: string): string {
  return `${origin}/share/${encodeURIComponent(token)}`;
}
