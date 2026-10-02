"use client";

import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "@/lib/api";
import type { KnowledgeReferenceSource } from "@/components/KnowledgeReferenceDetails";
import MessageMarkdown from "@/components/MessageMarkdown";
import ReasoningDetails from "@/components/ReasoningDetails";
import ReplyFeedback from "@/components/MessageFeedback";
import { CheckMark, CopyMark, EnsoMark, UserMark } from "@/components/icons";

export default function MessageRow({
  message,
  pending,
  replyWaiting = false,
  generationStartedAt,
  routingPending,
  animate,
  knowledgeSource,
  showDiagnostics = true,
}: {
  message: ChatMessage;
  pending: boolean;
  replyWaiting?: boolean;
  generationStartedAt?: number;
  routingPending: boolean;
  /** Only the newly submitted turn floats in; loaded history stays still. */
  animate: boolean;
  knowledgeSource?: KnowledgeReferenceSource;
  showDiagnostics?: boolean;
}) {
  const isUser = message.role === "user";
  const timestamp = message.created_at ? new Date(message.created_at) : null;
  const timeLabel = timestamp && Number.isFinite(timestamp.getTime())
    ? timestamp.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric",
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
    : null;
  const reasoning = message.reasoning_content?.trim() ?? "";
  const [copyState, setCopyState] = useState<"idle" | "copied" | "error">("idle");
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
    };
  }, []);

  async function handleCopy() {
    try {
      let copied = false;
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(message.content);
          copied = true;
        } catch {
          // Some embedded browsers expose the API but deny its permission.
          // Fall through to the selection-based path below in that case.
        }
      }

      if (!copied) {
        // Clipboard is unavailable in some embedded or older browsers. Keep a
        // synchronous fallback so the action still works there.
        const textarea = document.createElement("textarea");
        textarea.value = message.content;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand("copy");
        textarea.remove();
        if (!copied) throw new Error("Copy command was rejected");
      }
      setCopyState("copied");
    } catch {
      setCopyState("error");
    }

    if (copyResetRef.current) clearTimeout(copyResetRef.current);
    copyResetRef.current = setTimeout(() => setCopyState("idle"), 1800);
  }

  return (
    <div
      className={`mx-auto flex w-full max-w-5xl items-start gap-3 ${isUser ? "flex-row-reverse" : ""} ${
        animate ? (isUser ? "zen-message-user" : "zen-message-agent") : ""
      }`}
    >
      <Avatar isUser={isUser} />
      <div
        className={`group/message flex min-w-0 max-w-[min(90%,42rem)] items-end gap-1.5 ${
          isUser ? "flex-row-reverse" : ""
        }`}
      >
        <div className="min-w-0">
        {message.content && <div
          data-message-bubble={message.role}
          className={`min-w-0 max-w-[42rem] px-4 py-3 text-[0.95rem] leading-[1.85] tracking-[0.01em] break-words whitespace-pre-wrap sm:px-5 ${
            isUser
              ? "rounded-2xl rounded-tr-md bg-mine text-mine-ink depth-bubble"
              : "rounded-2xl rounded-tl-md bg-agent-bubble text-ink depth-bubble"
          }`}
        >
          {message.role === "assistant" ? <MessageMarkdown text={message.content}
            trailing={pending && replyWaiting ? <ReplyContinuationWait /> : undefined} /> : message.content}
          {!isUser && showDiagnostics && (
            <ReasoningDetails message={message} replyPending={pending && !routingPending} routingPending={routingPending} knowledgeSource={knowledgeSource} />
          )}
        </div>}
        {message.content && timeLabel && <time dateTime={message.created_at!}
          title="北京时间（UTC+08:00）"
          className={`mt-1 block px-1 text-[11px] text-ink-faint ${isUser ? "text-right" : ""}`}>
          {timeLabel} · 北京时间
        </time>}
        {!isUser && message.reply_status === "interrupted" && <p data-reply-interrupted
          className="mt-1 px-1 text-xs leading-5 text-ink-muted">回复已中止，已显示内容已保存。</p>}
        {!isUser && pending && !routingPending && !replyWaiting && <TypingDots startedAt={generationStartedAt} hasReasoning={Boolean(reasoning)} hasContent={Boolean(message.content)} />}
        {!isUser && !message.content && showDiagnostics && <ReasoningDetails message={message} replyPending={pending && !routingPending} routingPending={routingPending} knowledgeSource={knowledgeSource} />}
        {!isUser && message.content && <ReplyFeedback key={message.id} messageId={message.id} pending={pending}
          leadingAction={<>
            <button type="button" onClick={handleCopy} className="reply-vote-button"
              aria-label={copyState === "copied" ? "已复制消息" : "复制消息"}
              title={copyState === "error" ? "复制失败，请重试" : copyState === "copied" ? "已复制" : "复制"}>
              {copyState === "copied" ? <CheckMark className="h-4 w-4" /> : <CopyMark className="h-4 w-4" />}
            </button>
            <span className="sr-only" role="status">{copyState === "copied" ? "消息已复制" : copyState === "error" ? "复制失败，请重试" : ""}</span>
          </>} />}
        </div>
        {isUser && message.content && (
          <>
            <button
              type="button"
              onClick={handleCopy}
              aria-label={copyState === "copied" ? "已复制消息" : "复制消息"}
              title={copyState === "error" ? "复制失败，请重试" : copyState === "copied" ? "已复制" : "复制"}
              className={`mb-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-transparent transition-all duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-edge sm:opacity-0 sm:group-hover/message:opacity-100 sm:focus-visible:opacity-100 ${
                copyState === "error"
                  ? "border-alert-edge bg-alert-wash text-alert-ink opacity-100"
                  : copyState === "copied"
                    ? "border-accent-edge bg-accent-wash text-accent-ink opacity-100"
                    : "text-ink-faint opacity-60 hover:border-line hover:bg-accent-wash hover:text-accent-ink"
              }`}
            >
              {copyState === "copied" ? (
                <CheckMark className="h-4 w-4" />
              ) : (
                <CopyMark className="h-4 w-4" />
              )}
            </button>
            <span className="sr-only" role="status" aria-live="polite">
              {copyState === "copied" ? "消息已复制到剪贴板" : copyState === "error" ? "消息复制失败" : ""}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function ReplyContinuationWait() {
  return <span role="status" aria-label="正在生成深度回复" data-reply-wait
    className="ml-1.5 inline-flex h-4 items-center gap-1 align-baseline text-accent-ink">
    <span className="sr-only">正在生成深度回复</span>
    {[0, 1, 2].map(index => <span key={index} aria-hidden="true"
      className="zen-breathe inline-block h-1 w-1 rounded-full bg-current"
      style={{ animationDelay: `${index * 0.18}s` }} />)}
  </span>;
}

function Avatar({ isUser }: { isUser: boolean }) {
  return (
    <span
      className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${
        isUser
          ? "bg-mine text-accent"
          : "bg-accent-wash text-accent-ink"
      }`}
    >
      {isUser ? (
        <UserMark className="h-4 w-4" />
      ) : (
        <EnsoMark className="h-4 w-4" />
      )}
    </span>
  );
}

function TypingDots({ hasReasoning = false, hasContent = false, startedAt }: { hasReasoning?: boolean; hasContent?: boolean; startedAt?: number }) {
  const [seconds, setSeconds] = useState(() => startedAt ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) : 0);
  useEffect(() => {
    const started = startedAt ?? Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  return (
    <span data-generation-status className="flex min-w-0 items-start gap-2 px-1 py-2" aria-label="Thinking">
      <span aria-hidden="true" className="mt-1.5 flex shrink-0 items-center gap-1.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="zen-breathe h-1.5 w-1.5 rounded-full bg-accent"
            style={{ animationDelay: `${i * 0.18}s` }}
          />
        ))}
      </span>
      <span className="text-xs leading-relaxed text-ink-faint">{seconds >= 20 ? `仍在生成，已等待 ${seconds} 秒；请勿重复提交` : hasContent ? "正在生成回复" : hasReasoning ? "正在深度思考" : "正在准备回复"}</span>
    </span>
  );
}
