/** Client for the sidebar's conversation list, backed by `/api/conversations`. */

import { API_BASE, type ChatMessage } from "@/lib/api";
import { apiHeaders } from "@/lib/http";

export interface ConversationSummary {
  session_id: string;
  title: string;
  updated_at: string;
  pinned: boolean;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[];
  next_module: string | null;
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
  if (!res.ok) {
    const detail = await res.text();
    throw new ConversationRequestError(
      `${what} failed (${res.status}): ${detail}`,
      res.status,
    );
  }
  return res.json() as Promise<T>;
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
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  const res = await fetch(`${API_BASE}/api/conversations`, {
    method: "POST",
    headers: apiHeaders(),
    signal,
  });
  return parse(res, "Starting a conversation");
}

export async function fetchConversation(
  sessionId: string,
  signal?: AbortSignal,
): Promise<ConversationDetail> {
  const res = await fetch(
    `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}`,
    { headers: apiHeaders(), signal },
  );
  return parse(res, "Loading the conversation");
}

export async function fetchConversationRevision(
  sessionId: string,
  signal?: AbortSignal,
): Promise<{ revision: number }> {
  const res = await fetch(
    `${API_BASE}/api/conversations/${encodeURIComponent(sessionId)}/revision`,
    { headers: apiHeaders(), signal },
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
