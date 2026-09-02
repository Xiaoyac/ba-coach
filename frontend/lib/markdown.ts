/** Renders a conversation as plain "user：… / agent：…" markdown and triggers
 *  a browser download — no server round trip, since the client already has
 *  (or can fetch) the full transcript.
 */

import type { ChatMessage } from "@/lib/api";

export function conversationToMarkdown(title: string, messages: ChatMessage[]): string {
  const body = messages
    .filter((m) => m.content)
    .map((m) => `${m.role === "user" ? "user" : "agent"}：${m.content}`)
    .join("\n\n");
  return `# ${title || "对话记录"}\n\n${body}\n`;
}

/** Keeps the filename filesystem-safe across platforms without mangling CJK. */
function sanitizeFilename(name: string): string {
  return name.replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 60) || "对话记录";
}

export function downloadMarkdown(title: string, messages: ChatMessage[]): void {
  const content = conversationToMarkdown(title, messages);
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${sanitizeFilename(title)}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
