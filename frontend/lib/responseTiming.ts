import { apiHeaders } from "@/lib/http";
import { API_BASE } from "@/lib/api";

type ResponseTiming = {
  generationId: string; sessionId: string; userMessageId: number | null;
  startedAt: number; bodyOffset: number | null; firstBodyMs: number | null;
  ended: boolean; saved: boolean;
};
const timings = new Map<string, ResponseTiming>();
const listeners = new Set<() => void>();
let revision = 0;
const emit = () => { revision++; listeners.forEach(listener => listener()); };
export const subscribeResponseTiming = (listener: () => void) => {
  listeners.add(listener); return () => { listeners.delete(listener); };
};
export const responseTimingRevision = () => revision;

export function beginResponseTiming(generationId: string, sessionId: string, startedAt: number) {
  timings.set(generationId, { generationId, sessionId, startedAt, userMessageId: null,
    bodyOffset: null, firstBodyMs: null, ended: false, saved: false });
  while (timings.size > 256) timings.delete(timings.keys().next().value!);
  emit();
}
export function responseTiming(sessionId?: string, userMessageId?: number | null) {
  return [...timings.values()].reverse().find(item => item.sessionId === sessionId
    && (userMessageId === undefined || item.userMessageId === userMessageId));
}
async function persist(item: ResponseTiming) {
  if (item.saved || item.userMessageId === null || item.firstBodyMs === null) return;
  try {
    const result = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(item.sessionId)}/execution/client-timing`, {
      method: "POST", headers: apiHeaders({ json: true }),
      body: JSON.stringify({ user_message_id: item.userMessageId, first_body_ms: item.firstBodyMs }),
      signal: AbortSignal.timeout(8000),
    });
    item.saved = result.ok;
  } catch { /* Keep the local measurement; telemetry must never block a reply. */ }
}
export function bindResponseTiming(generationId: string, userMessageId: number) {
  const item = timings.get(generationId);
  if (!item) return;
  item.userMessageId = userMessageId; emit(); void persist(item);
}
export function noteBodyOffset(generationId: string, offset: number) {
  const item = timings.get(generationId);
  if (item && item.bodyOffset === null) { item.bodyOffset = offset; emit(); }
}
export function endResponseTiming(generationId: string) {
  const item = timings.get(generationId);
  if (item) { item.ended = true; emit(); }
}
export function markBodyPainted(sessionId: string, userMessageId: number) {
  const item = responseTiming(sessionId, userMessageId);
  if (!item || item.bodyOffset === null || item.firstBodyMs !== null) return;
  item.firstBodyMs = Math.max(0, Math.round(performance.now() - item.startedAt));
  emit(); void persist(item);
}
