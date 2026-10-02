/** Client for the sidebar's conversation list, backed by `/api/conversations`. */

import { API_BASE, type ChatMessage, type MessageRequests } from "@/lib/api";
import { apiHeaders, checkAuthentication } from "@/lib/http";

export interface ConversationSummary {
  session_id: string;
  title: string;
  updated_at: string;
  pinned: boolean;
}

export type ConversationRoutingMode = "router_code" | "router_only";
export const routingModeLabels: Record<ConversationRoutingMode, string> = {
  router_code: "Router + 代码",
  router_only: "仅 Router",
};

export type ConversationReplyMode = "standard" | "ack_deep";
export type ConversationReplyEffort = "low" | "high" | "max";
export const replyEffortLabels: Record<ConversationReplyEffort, string> = {
  low: "低（更快）", high: "高（更充分）", max: "最高（更慢）",
};
export const replyModeLabels: Record<ConversationReplyMode, string> = {
  standard: "现有回复",
  ack_deep: "自然接话＋深度回复（并行实验）",
};

export interface ConversationDetail extends ConversationSummary {
  revision?: number;
  thinking_enabled?: boolean | null;
  knowledge_mediator_enabled?: boolean;
  knowledge_mode?: "legacy_mediator" | "astrbot_hybrid";
  messages: ChatMessage[];
  next_module: string | null;
  routing_mode?: ConversationRoutingMode;
  reply_mode?: ConversationReplyMode;
  reply_effort?: ConversationReplyEffort | null;
  reply_effort_options?: ConversationReplyEffort[];
}

export interface ReferenceChunk {
  id: string;
  source: string;
  text: string;
  score: number | null;
  score_type?: string | null;
}

export interface KnowledgeReferences {
  available: boolean;
  module: string | null;
  retrieval_outcome: string | null;
  retrieval_mode?: string | null;
  gate_reason: string | null;
  mediator_status: string | null;
  mediator_reason: string | null;
  mediator_reasoning_content: string | null;
  mediator_guidance?: string | null;
  mediator_cautions?: string[];
  mediator_model: string | null;
  mediator_duration_ms: number | null;
  context_withheld: boolean;
  validator_status: string | null;
  recalled: ReferenceChunk[];
  provided: ReferenceChunk[];
}

export async function fetchKnowledgeReferences(messageId: number, signal?: AbortSignal): Promise<KnowledgeReferences> {
  const response = await fetch(`${API_BASE}/api/conversations/messages/${messageId}/knowledge`, {
    headers: apiHeaders(), signal, cache: "no-store",
  });
  return parse(response, "Loading knowledge references");
}

/**
 * Carries the HTTP status alongside the message so callers can react to a
 * specific failure — chiefly 404, which for these endpoints always means "this
 * conversation is gone" and should reconcile the sidebar rather than just
 * surface a string. Sniffing the status out of the message text would work
 * until someone reworded it.
 */
export class ConversationRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ConversationRequestError";
    this.status = status;
  }
}

export function isMissing(error: unknown): boolean {
  return error instanceof ConversationRequestError && error.status === 404;
}

async function parse<T>(res: Response, what: string): Promise<T> {
  checkAuthentication(res);
  if (!res.ok) {
    const detail = await res.text();
    throw new ConversationRequestError(
      `${what} failed (${res.status}): ${detail}`,
      res.status,
    );
  }
  return res.json() as Promise<T>;
}

export async function setConversationMediator(sessionId: string, enabled: boolean): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/knowledge-mediator`, {
    method: "PATCH", headers: apiHeaders({ json: true }), body: JSON.stringify({ enabled }),
  });
  return parse(res, "保存知识中介设置");
}

export async function setConversationReplyEffort(sessionId: string, effort: ConversationReplyEffort): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/reply-effort`, {
    method: "PATCH", headers: apiHeaders({ json: true }), body: JSON.stringify({ effort }),
  });
  return parse(res, "保存主回复思考强度");
}

export async function listConversations(
  signal?: AbortSignal,
): Promise<ConversationSummary[]> {
  const res = await fetch(`${API_BASE}/api/conversations`, {
    headers: apiHeaders(),
    signal,
  });
  return parse(res, "Loading conversations");
}

/** Create the durable opening turn and receive its server-owned session id. */
export async function createConversation(
  routingMode: ConversationRoutingMode,
  replyMode: ConversationReplyMode = "standard",
  replyEffort?: ConversationReplyEffort,
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/conversations`, {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify({ routing_mode: routingMode, reply_mode: replyMode,
      ...(replyMode === "ack_deep" && replyEffort ? { reply_effort: replyEffort } : {}) }),
    signal,
  });
  return parse(res, "Starting a conversation");
}

/** Resume the latest real chat, creating its opening once across tabs/devices. */
export async function getCurrentConversation(
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/conversations/current`, {
    method: "POST",
    headers: apiHeaders(),
    signal,
    cache: "no-store",
  });
  return parse(res, "Loading your conversation");
}

export async function fetchConversation(
  sessionId: string,
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  const res = await fetch(
    `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}`,
    { headers: apiHeaders(), signal, cache: "no-store" },
  );
  return parse(res, "Loading the conversation");
}

export async function fetchConversationRevision(
  sessionId: string,
  signal?: AbortSignal,
): Promise<{ revision: number }> {
  const res = await fetch(
    `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/revision`,
    { headers: apiHeaders(), signal, cache: "no-store" },
  );
  return parse(res, "Checking the conversation revision");
}

export interface ConversationLiveHandlers {
  onSnapshot: (detail: ConversationDetail) => void;
  onDeleted?: () => void;
}

/**
 * Keep one authenticated, fetch-based SSE stream open for the active chat.
 * Native EventSource cannot attach the bearer token, so this uses the same
 * framed-stream parser as chat streaming while preserving normal auth.
 */
export async function subscribeConversation(
  sessionId: string,
  handlers: ConversationLiveHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/events`,
    { headers: apiHeaders(), signal },
  );
  if (!res.ok || !res.body) {
    throw new ConversationRequestError(
      `Live conversation sync failed (${res.status})`,
      res.status,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trim());
      }
      if (event === "deleted") {
        handlers.onDeleted?.();
        return;
      }
      if (event === "snapshot" && data.length) {
        handlers.onSnapshot(JSON.parse(data.join("\n")) as ConversationDetail);
      }
    }
  }
}

/** Rename and/or pin. Omitted fields are left untouched by the server. */
export async function updateConversation(
  sessionId: string,
  patch: { title?: string; pinned?: boolean },
  signal?: AbortSignal,
): Promise<ConversationSummary> {
  const res = await fetch(
    `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}`,
    {
      method: "PATCH",
      headers: apiHeaders({ json: true }),
      body: JSON.stringify(patch),
      signal,
    },
  );
  return parse(res, "Updating the conversation");
}

export async function deleteConversation(
  sessionId: string,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}`,
    { method: "DELETE", headers: apiHeaders(), signal },
  );
  if (!res.ok && res.status !== 404) {
    const detail = await res.text();
    throw new Error(`Deleting the conversation failed (${res.status}): ${detail}`);
  }
}

export async function fetchMessageRequests(messageId: number, signal?: AbortSignal): Promise<MessageRequests> {
  const response = await fetch(`${API_BASE}/api/conversations/messages/${messageId}/requests`, {
    headers: apiHeaders(), signal, cache: "no-store",
  });
  return parse(response, "加载请求记录");
}

export interface ContextUsage {
  estimated_tokens: number;
  token_budget: number | null;
  retained_messages: number;
  summarized_messages: number;
  total_messages: number;
  message_budget: number | null;
  mode: "compression" | "window";
}

export async function fetchContextUsage(sessionId: string, signal?: AbortSignal): Promise<ContextUsage> {
  const response = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/context`, {
    headers: apiHeaders(), signal, cache: "no-store",
  });
  return parse(response, "读取上下文用量");
}
