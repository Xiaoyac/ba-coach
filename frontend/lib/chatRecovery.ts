import type { ConversationDetail } from "@/lib/conversations";
import { hasDurableReplyForTurn } from "@/lib/replyBinding";

export type FailedChatSend = {
  id: string;
  sessionId: string;
  text: string;
  userMessageId: number | null;
};

/** The tab journal starts before POST, not after a failure callback can unmount. */
export function acknowledgeChatSend(items: FailedChatSend[], id: string, userMessageId: number): FailedChatSend[] {
  if (!Number.isSafeInteger(userMessageId) || userMessageId <= 0) return items;
  return items.map(item => item.id === id ? { ...item, userMessageId } : item);
}

export function visibleFailedSends(items: FailedChatSend[], sessionId: string | null, runningId?: string): FailedChatSend[] {
  return items.filter(item => item.sessionId === sessionId && item.id !== runningId);
}

/** A missing receipt is unknown, never proof that it is safe to send again. */
export function reconcileFailedSends(items: FailedChatSend[], detail: ConversationDetail): FailedChatSend[] {
  const next = items.filter(item => !hasDurableReplyForTurn(detail, item));
  return next.length === items.length ? items : next;
}

export function restoreFailedText(current: string, item: FailedChatSend, sessionId?: string): string {
  return current || item.sessionId !== sessionId || item.userMessageId !== null ? current : item.text;
}

function storageKey(accountId: string) { return `bacoach:failed-chat-sends:${accountId}`; }

export function readFailedSends(accountId: string): FailedChatSend[] {
  if (!accountId) return [];
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(storageKey(accountId)) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is FailedChatSend => item && typeof item.id === "string"
      && typeof item.sessionId === "string" && typeof item.text === "string" && item.text.length > 0
      && (item.userMessageId === null || (Number.isSafeInteger(item.userMessageId) && item.userMessageId > 0)));
  } catch { return []; }
}

export function writeFailedSends(accountId: string, items: FailedChatSend[]): void {
  if (!accountId) return;
  try {
    if (items.length) sessionStorage.setItem(storageKey(accountId), JSON.stringify(items));
    else sessionStorage.removeItem(storageKey(accountId));
  } catch { /* The in-memory copy remains available if tab storage is blocked. */ }
}

/** Only local text reaches the UI; upstream bodies may contain keys or traces. */
export function chatRequestError(status: number): string {
  if (status === 401) return "登录已失效，请重新登录；已保存的记录不会丢失。";
  if (status === 403) return "当前账号暂时无法执行此操作，请联系管理员。";
  if (status === 404) return "这条对话已不可访问，请刷新对话列表。";
  if (status === 409) return "本次请求状态有冲突，正在同步记录，请勿重复发送。";
  if (status === 429) return "当前请求较多，请稍后查看记录，再决定是否重试。";
  if (status === 400 || status === 422) return "本次请求未能通过检查，请核对内容后手动重试。";
  if (status === 502 || status === 503 || status === 504) return "服务暂时不可用，正在同步记录。原文会保留，请勿重复发送。";
  return "本次请求暂时失败，正在同步记录。原文会保留，请勿重复发送。";
}

export function safeGenerationError(detail: unknown): string {
  const value = typeof detail === "string" ? detail : "";
  if (/^reply_length_limit(?:[：:]|$)/.test(value)) return "本次回复因长度限制未完成。请查看已显示内容；不会自动续写或重发。";
  if (/额度|quota|insufficient.*(balance|credit)|余额/i.test(value)) return "模型服务额度不足，暂时无法回复，请联系管理员。";
  if (/timeout|timed out|超时/i.test(value)) return "模型回应超时，正在同步记录，请勿重复发送。";
  if (/rate.?limit|too many|限流/i.test(value)) return "模型服务当前较忙，请稍后查看记录，再决定是否重试。";
  return "模型暂时未能完成回复，正在同步记录，请勿重复发送。";
}

export function safeChatTransportError(error: unknown): string {
  const value = error instanceof Error ? error.message : "";
  if (value === chatRequestError(401)) return value;
  // streamChat deliberately throws only these local, safe generation messages.
  if ([safeGenerationError(""), safeGenerationError("quota"), safeGenerationError("timeout"), safeGenerationError("rate limit"), safeGenerationError("reply_length_limit")].includes(value)) return value;
  return "连接已中断，正在同步已保存的回复。原文会保留，请勿重复发送。";
}
