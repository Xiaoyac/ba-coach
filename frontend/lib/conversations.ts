/** Client for the sidebar's conversation list, backed by `/api/conversations`. */

import { API_BASE, type ChatMessage, type MessageRequests } from "@/lib/api";
import { apiHeaders, checkAuthentication } from "@/lib/http";
import { chatRequestError } from "@/lib/chatRecovery";

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

export type ConversationReplyEffort = "low" | "high" | "max";
export const replyEffortLabels: Record<ConversationReplyEffort, string> = {
  low: "低（更快）", high: "高（更充分）", max: "最高（更慢）",
};

export interface ConversationDetail extends ConversationSummary {
  revision?: number;
  thinking_enabled?: boolean | null;
  knowledge_mediator_enabled?: boolean; // compatibility: selects legacy retrieval
  knowledge_mediator_node_enabled?: boolean;
  knowledge_mode?: "legacy_mediator" | "astrbot_hybrid";
  messages: ChatMessage[];
  next_module: string | null;
  routing_mode?: ConversationRoutingMode;
  reply_mode?: "ack_deep";
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
  mediator_validation_errors?: { field: string; type: string }[];
  mediator_normalized_fields?: string[];
  mediator_reasoning_content: string | null;
  mediator_response_content?: string | null;
  mediator_note?: string | null;
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
    throw new ConversationRequestError(
      chatRequestError(res.status),
      res.status,
    );
  }
  try { return await res.json() as T; }
  catch { throw new ConversationRequestError("对话数据暂时无法读取，请稍后重试。", 502); }
}

export async function setConversationMediatorNode(sessionId: string, enabled: boolean): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/mediator-node`, {
    method: "PATCH", headers: apiHeaders({ json: true }), body: JSON.stringify({ enabled }),
  });
  return parse(res, "保存中介节点设置");
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
  replyEffort?: ConversationReplyEffort,
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/conversations`, {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify({ routing_mode: routingMode, ...(replyEffort ? { reply_effort: replyEffort } : {}) }),
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
  const res = await fetchHistory(
    `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}`,
    signal,
  );
  return parse(res, "Loading the conversation");
}

export async function fetchConversationRevision(
  sessionId: string,
  signal?: AbortSignal,
): Promise<{ revision: number }> {
  const res = await fetchHistory(
    `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/revision`,
    signal,
  );
  return parse(res, "Checking the conversation revision");
}

function historyReadSignal(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(10000);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

async function fetchHistory(url: string, signal?: AbortSignal): Promise<Response> {
  try { return await fetch(url, { headers: apiHeaders(), signal: historyReadSignal(signal), cache: "no-store" }); }
  catch (error) {
    if (signal?.aborted) throw error;
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    throw new Error(timedOut ? "对话记录读取超时，请稍后重试。" : "暂时无法同步对话记录，请检查网络后重试。");
  }
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

export interface ExecutionStage {
  stage: string; label: string; lane: "foreground" | "parallel";
  status: "running" | "retrying" | "publishing" | "completed" | "failed" | "cancelled" | "skipped";
  model?: string | null; duration_ms?: number | null; elapsed_ms?: number;
  detail?: string | null; calls: number;
  failure?: { code: string; reason: string; suggestion: string; http_status?: number; request_id?: string } | null;
}
export interface ConversationExecution {
  client_first_body_ms?: number | null;
  session_id: string; user_message_id: number | null; module: string | null;
  live: boolean; request_finished: boolean; server_time: number;
  state: "empty" | "running" | "background" | "completed" | "completed_with_errors" | "failed" | "cancelled" | "incomplete";
  stages: ExecutionStage[];
  turns: {id: number; preview: string; created_at: string}[];
}
export interface ExecutionHistoryPage {
  session_id: string;
  current_user_message_id: number | null;
  turns: ConversationExecution["turns"];
  next_before_id: number | null;
}
export async function fetchExecutionHistory(sessionId: string, beforeId: number | null, signal?: AbortSignal): Promise<ExecutionHistoryPage> {
  const query = beforeId === null ? "" : `?before_id=${beforeId}`;
  const response = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/execution/history${query}`, {
    headers: apiHeaders(), signal, cache: "no-store",
  });
  return parse(response, "读取历史运行记录");
}
export async function fetchConversationExecution(sessionId: string, userMessageId: number | null, signal?: AbortSignal): Promise<ConversationExecution> {
  const query = userMessageId === null ? "" : `?user_message_id=${userMessageId}`;
  const response = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/execution${query}`, {
    headers: apiHeaders(), signal, cache: "no-store",
  });
  return parse(response, "读取模型运行状态");
}
