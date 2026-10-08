"use client";

import { useId } from "react";
import { CloseMark } from "@/components/icons";

/** Explanations describe the existing scales; they do not change the questions. */
export const DAILY_TERM_HELP: Record<string, string> = {
  completion_rate: "回想这一天你想做的事情，按实际完成的程度评分。这里不只指运动，也包括生活、学习或工作中的事情。0 表示几乎没完成，5 表示都完成了。",
  activity_level: "身体活动不只包括专门运动，也包括走路、做家务等让身体动起来的活动。按这一天的整体情况评分：0 表示几乎没有活动，5 表示活动很多。没有运动，也可以如实记录这一天。",
  overall_mood: "回顾一整天的总体心情，不只看某一次活动或此刻的感受。0 表示很低落，5 表示很愉快；选最接近你感受的分数即可。",
  emotion: "这是做完这一项活动之后的心情，与整天的总体心情分开记录。0 表示很低落，5 表示很愉快。",
  achievement: "做这件事时，你有多少“我做到了”的感觉。例如完成一件小事、学会一点东西。事情不必很难或很大。0 表示很少，5 表示很多。",
  connection: "这件事让你感到与别人有多少联系、被理解或被支持。例如和朋友聊了几句。不是计算见了多少人。0 表示很少，5 表示很多。",
  enjoyment: "做这件事时，你感到多少开心、舒服或享受。它可以和成就感不同。0 表示很少，5 表示很多。",
  importance: "这件事对你本人有多少意义，是否贴近你在意的事情。按自己的感受评分，不按别人认为它有多重要。0 表示很少，5 表示很多。",
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
