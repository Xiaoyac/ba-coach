import type { ConversationDetail } from "@/lib/conversations";

/** Fail closed until the POST stream identifies its persisted user message. */
export function hasDurableReplyForTurn(
  detail: ConversationDetail,
  turn: { sessionId: string; userMessageId: number | null },
): boolean {
  if (detail.session_id !== turn.sessionId || turn.userMessageId === null) return false;
  if (!detail.messages.some(m => m.role === "user" && m.id === turn.userMessageId)) return false;
  return detail.messages.some(m =>
    m.role === "assistant" && m.reply_to_message_id === turn.userMessageId && Boolean(m.content?.trim()),
  );
}
