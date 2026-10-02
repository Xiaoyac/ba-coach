import { API_BASE } from "@/lib/api";
import { apiHeaders, checkAuthentication } from "@/lib/http";

export const feedbackReasons = {
  off_topic: "答非所问", repetitive: "重复追问", incomplete: "回复不完整",
  incorrect: "理解有误", uncomfortable: "让我不舒服", other: "其他",
} as const;
export type FeedbackReason = keyof typeof feedbackReasons;
export type FeedbackInput = { rating: "up" | "down" | null; reasons?: FeedbackReason[]; comment?: string };
export type MessageFeedback = {
  message_id: number; rating: "up" | "down"; reasons: FeedbackReason[];
  comment: string; updated_at: string;
};
export type AdminMessageFeedback = MessageFeedback & {
  session_id: string; title: string; content: string; model: string | null; request_id: string | null;
};

async function read<T>(response: Response): Promise<T> {
  checkAuthentication(response);
  if (!response.ok) throw new Error("反馈未保存，请稍后重试");
  return response.json() as Promise<T>;
}

export async function fetchMessageFeedback(sessionId: string, signal?: AbortSignal) {
  return read<MessageFeedback[]>(await fetch(`${API_BASE}/api/message-feedback?session_id=${encodeURIComponent(sessionId)}`,
    { headers: apiHeaders(), signal, cache: "no-store" }));
}

export async function saveMessageFeedback(id: number, input: FeedbackInput) {
  return read<MessageFeedback | null>(await fetch(`${API_BASE}/api/message-feedback/${id}`, {
    method: "PUT", headers: apiHeaders({ json: true }), body: JSON.stringify(input),
  }));
}

export async function fetchAdminMessageFeedback(rating: "all" | "up" | "down", before?: number, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: "30" });
  if (rating !== "all") params.set("rating", rating);
  if (before) params.set("before", String(before));
  return read<{ items: AdminMessageFeedback[]; next_cursor: number | null }>(await fetch(
    `${API_BASE}/api/admin/message-feedback?${params}`, { headers: apiHeaders(), signal, cache: "no-store" },
  ));
}
