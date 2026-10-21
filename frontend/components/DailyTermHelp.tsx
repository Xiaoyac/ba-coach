"use client";

import { useId } from "react";
import { CloseMark } from "@/components/icons";

/** Explanations describe the existing scales; they do not change the questions. */
export const DAILY_TERM_HELP: Record<string, string> = {
  emotion: "做这件事时，你的心情如何？（0=非常糟糕，5=非常愉悦）",
  achievement: "做这件事让你觉得自己有成就感吗？",
  connection: "做这件事让你感觉与他人有连接吗？",
  enjoyment: "做这件事让你感到享受或快乐吗？",
  importance: "你觉得它是一件重要、值得做的事吗？",
  social_connection: "回顾这一天，你感到与他人有多少联系、亲近或支持。这是旧版记录中的整天评分，请沿用这份记录显示的 0–10 分量表。",
  approach_vs_avoidance: "回顾这一天，面对在意或需要处理的事情时，你更常尝试面对，还是选择躲开、拖延。这是旧版的自我记录，不是对表现的评判；请沿用显示的 0–10 分量表。",
};

/** Native popover supports touch, outside click, Escape and return focus.
 * A centered panel avoids being clipped by a narrow scale or modal scroller. */
export default function DailyTermHelp({ label, explanation }: { label: string; explanation: string }) {
  const id = useId();
  return <>
    <button type="button" popoverTarget={id} aria-label={`解释：${label}`} aria-haspopup="dialog"
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-accent-ink hover:bg-accent-wash focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
      <span aria-hidden="true" className="flex h-5 w-5 items-center justify-center rounded-full border border-accent-edge">?</span>
    </button>
    <div id={id} popover="auto" role="dialog" aria-labelledby={`${id}-title`} aria-describedby={`${id}-text`}
      className="fixed inset-0 m-auto h-fit max-h-[80dvh] w-[min(25rem,calc(100vw-2rem))] overflow-y-auto rounded-2xl border border-accent-edge bg-sheet p-5 text-ink shadow-xl backdrop:bg-black/15">
      <div className="flex items-center justify-between gap-3">
        <h4 id={`${id}-title`} className="text-base font-semibold">{label}</h4>
        <button type="button" popoverTarget={id} popoverTargetAction="hide" aria-label="关闭解释"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-ink-muted hover:bg-raised">
          <CloseMark className="h-4 w-4" />
        </button>
      </div>
      <p id={`${id}-text`} className="mt-2 text-sm leading-7 text-ink-muted">{explanation}</p>
    </div>
  </>;
}
