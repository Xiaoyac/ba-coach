import { apiHeaders, checkAuthentication } from "@/lib/http";

/**
 * Where the backend lives, from the browser's point of view.
 *
 * `next.config.mjs` proxies `/api/*` through the Next.js server to
 * `BACKEND_ORIGIN` (default `http://127.0.0.1:8000`), so a same-origin
 * relative path is *always* correct: locally, over the LAN, or through a
 * single ngrok tunnel on the frontend — the browser never talks to the
 * backend directly, so it never has an origin to get wrong. See "Exposing
 * via ngrok" in the README.
 *
 * `NEXT_PUBLIC_API_BASE_URL` remains as an escape hatch for topologies where
 * that proxy isn't wanted — frontend and backend deployed to genuinely
 * separate hosts in production, for instance. Like all `NEXT_PUBLIC_*`
 * values it is inlined into the client bundle when the server starts (or at
 * `next build`), so changing it needs a restart, never just a reload.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type Role = "user" | "assistant";

export interface ChatMessage {
  id?: number | null;
  role: Role;
  content: string;
  /** Provider-supplied thinking, disclosed separately from the final answer. */
  reasoning_content?: string | null;
  model_name?: string | null;
  routing_reasoning_content?: string | null;
  router_model_name?: string | null;
  timing?: {
    reply_thinking_ms: number | null;
    reply_generation_ms: number | null;
    router_processing_ms: number | null;
  } | null;
}

export interface ChatRequest {
  message: string;
  generation_id?: string;
  session_id?: string | null;
  provider?: "claude" | "deepseek" | "doubao";
  /** Pin a module and skip the router, e.g. "module_3". */
  module?: string;
  metadata?: Record<string, string>;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  reasoning_content: string;
  routing_reasoning_content: string;
  router_model_name: string;
  provider: string;
  model: string;
  reply_module: string;
  next_module: string | null;
  routing_pending: boolean;
  routed_by: string;
  usage: Record<string, number>;
}

/** Module names the backend accepts for `ChatRequest.module`. */
export async function listModules(): Promise<string[]> {
  const res = await fetch(`${API_BASE}/api/modules`, { headers: apiHeaders() });
  if (!res.ok) throw new Error(`Could not load modules (${res.status})`);
  return (await res.json()).modules;
}

/** Non-streaming call — one request, one full reply. */
export async function sendChat(body: ChatRequest): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Chat request failed (${res.status}): ${detail}`);
  }
  return res.json();
}

export interface RoutingMeta {
  session_id: string;
  provider: string;
  model: string;
  /** Module that generated the latest visible assistant reply. */
  reply_module: string;
  /** Durable module pointer for the next user turn. */
  next_module: string | null;
  routing_pending: boolean;
  routed_by: string;
}

export interface StreamHandlers {
  onCancelled?: () => void;
  /**
   * Fired one or more times before the deltas start. The stream emits meta in
   * two parts: the route sends `session_id`/`provider` immediately, then the
   * graph's analyze_intent node sends `reply_module`/`routed_by` once it has
   * decided. The eventual `next_module` arrives through conversation sync
   * after the background Router Agent completes.
   * Merge them rather than expecting a single complete object.
   */
  onMeta?: (meta: Partial<RoutingMeta>) => void;
  onReasoningDelta?: (text: string) => void;
  onRoutingReasoning?: (text: string, model: string) => void;
  onDelta?: (text: string) => void;
  onDone?: () => void;
  onError?: (message: string) => void;
}

/**
 * Streaming call. EventSource can't issue a POST, so this reads the SSE body
 * off `fetch` directly and parses `event:` / `data:` frames by hand.
 */
export async function streamChat(
  body: ChatRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify(body),
    signal,
  });
  checkAuthentication(res);

  if (!res.ok || !res.body) {
    handlers.onError?.(`Chat request failed (${res.status})`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let replyReceived = false;
  let generationError: string | null = null;

  try { while (true) {
    const { done, value } = await reader.read();
    if (done) throw new Error(generationError ?? "连接已中断，正在同步已保存的回复；请勿重复发送。");
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");

    // SSE frames are separated by a blank line.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;

      const payload = JSON.parse(dataLines.join("\n"));
      switch (event) {
        case "meta":
          handlers.onMeta?.(payload as Partial<RoutingMeta>);
          break;
        case "delta":
          if (typeof payload.text === "string" && payload.text.trim()) replyReceived = true;
          handlers.onDelta?.(payload.text);
          break;
        case "reasoning_delta":
          handlers.onReasoningDelta?.(payload.text);
          break;
        case "routing_reasoning":
          handlers.onRoutingReasoning?.(payload.text, payload.model ?? "");
          break;
        case "error":
          generationError = typeof payload.detail === "string" && payload.detail.trim()
            ? payload.detail as string : "模型生成失败，请稍后重试或切换模型。";
          handlers.onError?.(generationError);
          break;
        case "persisted":
          handlers.onDone?.();
          if (payload.saved === false) {
            if (replyReceived) {
              const saveError = "回复已生成，但保存失败，请先复制回复后再刷新。";
              handlers.onError?.(generationError ? `${generationError}\n${saveError}` : saveError);
            } else if (!generationError) {
              handlers.onError?.("模型未返回回复正文，请稍后重试或切换模型。");
            }
          }
          return;
        case "cancelled":
          handlers.onCancelled?.();
          handlers.onDone?.();
          return;
      }
    }
  } } catch (error) {
    // A later EOF/network error must not hide the upstream failure already shown.
    if (generationError && !signal?.aborted) throw new Error(generationError);
    throw error;
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export async function cancelGeneration(generationId: string): Promise<{ status: "cancelled" | "stopping" | "finalizing" | "finished" }> {
  const res = await fetch(`${API_BASE}/api/chat/cancel`, {
    method: "POST", headers: apiHeaders({ json: true }),
    body: JSON.stringify({ generation_id: generationId }), signal: AbortSignal.timeout(12000),
  });
  checkAuthentication(res);
  if (!res.ok) throw new Error("停止请求未确认，请重试。当前回复可能仍在生成。");
  return res.json();
}

/** Timing can arrive after identical streamed text; don't drop that enrichment. */
export function sameMessageTiming(a: ChatMessage, b: ChatMessage): boolean {
  return (a.id ?? null) === (b.id ?? null)
    && (a.timing?.reply_thinking_ms ?? null) === (b.timing?.reply_thinking_ms ?? null)
    && (a.timing?.reply_generation_ms ?? null) === (b.timing?.reply_generation_ms ?? null)
    && (a.timing?.router_processing_ms ?? null) === (b.timing?.router_processing_ms ?? null);
}
