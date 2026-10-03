"use client";

import { useEffect, useRef, useState } from "react";
import { fetchGoalCard, submitGoalCard, goalCardFieldLabels, goalCardFieldValue, goalCardKind,
  type FormulationCard, type GoalCardFields } from "@/lib/goal-card";

type Props = {
  sessionId?: string; accountKey: string; module?: string | null; busy: boolean; loading: boolean;
  refreshKey: string; onSend: (text: string, metadata?: Record<string, string>) => Promise<void>; onOpenGoals: () => void;
  onUpdated?: () => void;
};
type Draft = Record<keyof GoalCardFields, string>;
const keys = Object.keys(goalCardFieldLabels) as (keyof GoalCardFields)[];
const numeric = new Set<keyof GoalCardFields>(["duration_minutes", "difficulty_rating"]);
const maxLength: Partial<Record<keyof GoalCardFields, number>> = {
  activity_content: 255, schedule_text: 255, location: 255, frequency_text: 255,
  long_term_direction: 1000, potential_barriers: 2000, barrier_coping_plan: 4000,
};
function draftOf(fields: GoalCardFields): Draft {
  return Object.fromEntries(keys.map(key => [key, fields[key] == null ? "" : String(fields[key])])) as Draft;
}
function fieldsOf(draft: Draft, kind: FormulationCard["kind"]): GoalCardFields {
  return Object.fromEntries(keys.filter(key => kind === "primary" || !["difficulty_rating", "potential_barriers", "barrier_coping_plan", "long_term_direction"].includes(key))
    .map(key => [key, numeric.has(key) ? draft[key] === "" ? null : Number(draft[key]) : draft[key].trim()]));
}
function storageGet(key: string): string | null { try { return sessionStorage.getItem(key); } catch { return null; } }
function storageSet(key: string, value: string): void { try { sessionStorage.setItem(key, value); } catch { /* Private browsing may disable storage; in-memory state still works. */ } }
function storageRemove(key: string): void { try { sessionStorage.removeItem(key); } catch { /* Same fallback. */ } }

export default function GoalFormulationPanel({sessionId, accountKey, module, busy, loading, refreshKey, onSend, onOpenGoals, onUpdated}: Props) {
  const [card, setCard] = useState<FormulationCard | null>(null);
  const [draft, setDraft] = useState<Draft>(() => draftOf({}));
  const [expanded, setExpanded] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(0);
  const [enabled, setEnabled] = useState(true);
  const cardRef = useRef<FormulationCard | null>(null);
  const dirtyRef = useRef(false);
  const changedFields = useRef(new Set<keyof GoalCardFields>());
  const mounted = useRef(true);
  const mutation = useRef<AbortController | null>(null);
  const savingRef = useRef(false);
  const seen = useRef(new Set<string>());
  const prefix = `bacoach-goal-card:${accountKey}:${sessionId ?? "none"}`;
  const prefixRef = useRef(prefix);
  prefixRef.current = prefix;
  const onUpdatedRef = useRef(onUpdated);
  onUpdatedRef.current = onUpdated;

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; mutation.current?.abort(); }; }, []);

  useEffect(() => {
    if (!sessionId || loading) return;
    let active = true;
    let request: AbortController | null = null;
    let readInFlight = false;
    const originalPrefix = prefix;
    const refresh = async () => {
      if (savingRef.current || readInFlight || document.visibilityState === "hidden") return;
      readInFlight = true;
      request = new AbortController();
      const controller = request;
      try {
        const result = await fetchGoalCard(sessionId, AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]));
        if (!active || controller.signal.aborted || savingRef.current || originalPrefix !== prefixRef.current) return;
        setEnabled(result.enabled);
        const next = result.card;
        const previous = cardRef.current;
        if (next && previous?.id === next.id && next.revision < previous.revision) return;
        // An absent server card must never erase an unsent form.
        if (!next && dirtyRef.current) return;
        cardRef.current = next;
        setCard(next);
        setReadError("");
        if (!next) { setNotice(""); setExpanded(false); return; }
        const changedCard = previous?.id !== next.id;
        if (changedCard) {
          const cached = storageGet(`${originalPrefix}:draft:${next.id}`);
          let restored: Draft | null = null;
          changedFields.current.clear();
          if (cached) { try {
            const parsed = JSON.parse(cached), values = parsed.values ?? parsed;
            if (keys.every(key => typeof values[key] === "string")) {
              restored = values;
              changedFields.current = new Set(Array.isArray(parsed.changed) ? parsed.changed.filter((key: keyof GoalCardFields) => keys.includes(key)) : keys);
            }
          } catch { /* Ignore only a malformed local draft. */ } }
          dirtyRef.current = restored !== null;
          setDirty(restored !== null);
          setDraft(restored ? Object.fromEntries(keys.map(key => [key, changedFields.current.has(key) ? restored![key] : draftOf(next.fields)[key]])) as Draft : draftOf(next.fields));
          setNotice(restored ? "你尚未提交的填写已恢复。" : "");
          setExpanded(false);
        } else if (!dirtyRef.current) setDraft(draftOf(next.fields));
        else {
          // Keep typed fields while adopting new information the coach added
          // elsewhere. Submitting a partial edit must not erase fresh fields.
          setDraft(previousDraft => Object.fromEntries(keys.map(key => [key, changedFields.current.has(key) ? previousDraft[key] : draftOf(next.fields)[key]])) as Draft);
          if (next.revision > (previous?.revision ?? 0)) setNotice("教练更新了卡片。你正在填写的内容已保留，请核对后再提交。");
        }
        const seenKey = `${originalPrefix}:seen:${next.id}`;
        if (next.phase === "formulating" && !seen.current.has(seenKey) && !storageGet(seenKey)) {
          seen.current.add(seenKey); storageSet(seenKey, "1"); setExpanded(true);
        }
        if (next.phase === "confirmed" && (changedCard || previous?.phase !== "confirmed")) {
          setExpanded(false);
          setNotice("目标信息已保存到“我的目标”。");
          onUpdatedRef.current?.();
        }
        if (next.phase === "paused" && previous?.phase !== "paused") setExpanded(false);
      } catch (reason) {
        if (active && !controller.signal.aborted && originalPrefix === prefixRef.current) setReadError(reason instanceof Error ? reason.message : "目标卡暂时无法读取，请重试。");
      } finally { readInFlight = false; }
    };
    void refresh();
    const interval = window.setInterval(() => void refresh(), busy ? 2000 : 10000);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => { active = false; request?.abort(); window.clearInterval(interval); window.removeEventListener("focus", onFocus); document.removeEventListener("visibilitychange", onFocus); };
  }, [sessionId, prefix, loading, busy, refreshKey, retry]);

  function change(key: keyof GoalCardFields, value: string) {
    if (!card) return;
    dirtyRef.current = true; setDirty(true); setNotice("");
    changedFields.current.add(key);
    setDraft(previous => { const next = {...previous, [key]: value}; storageSet(`${prefix}:draft:${card.id}`, JSON.stringify({values:next,changed:[...changedFields.current]})); return next; });
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!card || !sessionId || savingRef.current || busy || loading) return;
    const targetPrefix = prefix;
    const controller = new AbortController(); mutation.current = controller;
    savingRef.current = true; setSaving(true); setError(""); setNotice("");
    try {
      const result = await submitGoalCard(sessionId, card, fieldsOf(draft, card.kind), AbortSignal.any([controller.signal, AbortSignal.timeout(30000)]));
      if (!mounted.current || controller.signal.aborted || prefixRef.current !== targetPrefix) return;
      if (!result.card || !result.submission_text) throw new Error("草稿已提交，但暂时无法开始讨论。你的填写仍保留，请重试。");
      cardRef.current = result.card; setCard(result.card);
      // Only the successful server save makes this draft clean. Failed writes
      // leave every field and its recovery copy intact.
      dirtyRef.current = false; changedFields.current.clear(); setDirty(false); setDraft(draftOf(result.card.fields));
      storageRemove(`${targetPrefix}:draft:${card.id}`);
      setExpanded(false); setNotice("草稿已保存，接着和教练一起看看。草稿还不代表确认执行。");
      onUpdatedRef.current?.();
      await onSend(result.submission_text, {goal_card_id: result.card.id, goal_card_revision: String(result.card.revision), goal_card_action: "submit"});
    } catch (reason) {
      if (mounted.current && !controller.signal.aborted && prefixRef.current === targetPrefix) setError(reason instanceof Error ? reason.message : "提交失败，填写内容已保留。");
    } finally {
      if (mounted.current && prefixRef.current === targetPrefix) { savingRef.current = false; setSaving(false); setRetry(value => value + 1); }
    }
  }

  async function sendChoice(action: "confirm" | "pause") {
    if (!card || !sessionId || busy || loading || savingRef.current) return;
    savingRef.current = true; setSaving(true); setError("");
    const targetPrefix = prefix;
    const controller = new AbortController(); mutation.current = controller;
    try {
      let chosen = card;
      // Pausing after typing preserves that partial draft on the server too.
      if (action === "pause" && dirtyRef.current) {
        const result = await submitGoalCard(sessionId, card, fieldsOf(draft, card.kind), AbortSignal.any([controller.signal, AbortSignal.timeout(30000)]));
        if (!mounted.current || controller.signal.aborted || prefixRef.current !== targetPrefix) return;
        if (!result.card) throw new Error("草稿暂时无法保存，填写内容仍保留。");
        chosen = result.card; cardRef.current = chosen; setCard(chosen);
        dirtyRef.current = false; changedFields.current.clear(); setDirty(false); setDraft(draftOf(chosen.fields));
        storageRemove(`${targetPrefix}:draft:${card.id}`);
      }
      const text = action === "confirm" ? `确认这张${goalCardKind(chosen.kind)}卡（第 ${chosen.revision} 版），就按这个安排。` : `先不用继续细化这张${goalCardKind(chosen.kind)}卡，保留草稿。`;
      await onSend(text, {goal_card_id: chosen.id, goal_card_revision: String(chosen.revision), goal_card_action: action});
    }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : "暂时无法发送，请重试。"); }
    finally { if (mounted.current) { savingRef.current = false; setSaving(false); setRetry(value => value + 1); } }
  }

  async function resume() {
    if (!card || busy || loading || savingRef.current) return;
    savingRef.current = true; setSaving(true); setError("");
    try { await onSend(`我想继续完善之前暂存的${goalCardKind(card.kind)}卡。`); }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : "暂时无法发送，请重试。"); }
    finally { if (mounted.current) { savingRef.current = false; setSaving(false); setRetry(value => value + 1); } }
  }

  const editable = card && !["confirmed", "paused"].includes(card.phase);
  const isM2 = /^module[_-]?2$/i.test(module ?? "");
  const kind = card ? goalCardKind(card.kind) : "目标";
  const phaseLabel = !card ? "" : {formulating:"先填想到的",discussing:"一起讨论中",ready:"等待你确认",confirmed:"已保存",paused:"草稿已保留"}[card.phase];
  const fieldKeys = card?.kind === "secondary" ? keys.filter(key => !["difficulty_rating", "potential_barriers", "barrier_coping_plan", "long_term_direction"].includes(key)) : keys;
  const lock = busy || loading || saving;
  return <section aria-label="目标卡快捷入口" className={`shrink-0 border-b border-line px-4 py-2 sm:px-8 ${isM2 && enabled ? "bg-accent-wash/65" : "bg-sheet"}`}>
    <div className="mx-auto max-w-[58rem]">
      <div className="flex min-h-10 items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-ink">{card ? `${kind}卡` : "我的目标"}{card && <span className="ml-2 text-xs font-normal text-ink-muted">{phaseLabel}</span>}</p>
          {!card && isM2 && enabled && <p className="mt-1 text-xs leading-5 text-ink-muted">先聊聊你的想法，开始制定目标时，卡片会在这里展开。</p>}
          {card && !expanded && <p className="mt-1 truncate text-xs text-ink-muted">{card.fields.activity_content || "想做什么、怎么安排，可以想到多少先填多少。"}</p>}
        </div>
        {card && <button type="button" onClick={() => setExpanded(value => !value)} aria-expanded={expanded} aria-controls="goal-card-editor" className="min-h-10 shrink-0 rounded-xl px-3 text-sm font-medium text-accent-ink hover:bg-raised">{expanded ? "收起" : editable ? dirty ? "继续填写" : "打开卡片" : "查看卡片"}</button>}
        <button type="button" onClick={onOpenGoals} aria-haspopup="dialog" className="min-h-10 shrink-0 rounded-xl border border-line px-3 text-xs text-ink-muted hover:bg-raised">{card ? "我的目标 ↗" : "查看全部 ↗"}</button>
      </div>
      {(error || readError) && <p role="alert" className="mt-2 text-xs leading-5 text-alert-ink">{error || readError} <button type="button" disabled={saving} onClick={() => setRetry(value => value + 1)} className="min-h-8 px-2 underline underline-offset-4">重试读取</button>{dirty && "未提交的填写仍保留。"}</p>}
      {notice && <p role="status" className="mt-1 text-xs leading-5 text-accent-ink">{notice}</p>}
      {card?.phase === "confirmed" && dirty && <p className="mt-1 text-xs leading-5 text-ink-muted">本次保存不包含你尚未提交的填写；这些内容仍保留在下方卡片中。</p>}
      {card && expanded && <div id="goal-card-editor" className="zen-scroll mt-3 max-h-[min(35dvh,420px)] overflow-y-auto rounded-2xl border border-accent-edge bg-panel p-4 sm:p-5">
        {editable ? <form onSubmit={event => void submit(event)}>
          <div className="mb-4"><h3 className="text-base font-semibold">先写下你想到的</h3><p className="mt-1 text-xs leading-5 text-ink-muted">都可以先留空。填完后一起讨论，确认之前只是一份草稿。{card.kind === "secondary" ? "这是额外活动，不会替换原来的核心目标。" : "活动、时间和难度，都可以和教练再调整。"}</p></div>
          <div className="grid gap-4 sm:grid-cols-2">
            {fieldKeys.map(key => <label key={key} className={`block min-w-0 ${key === "activity_content" ? "sm:col-span-2" : ""}`}>
              <span className="mb-1.5 block text-xs font-medium text-ink-muted">{goalCardFieldLabels[key]}{key === "difficulty_rating" ? "（0～10，可不填）" : ""}</span>
              {key === "activity_content" || key === "potential_barriers" || key === "barrier_coping_plan" || key === "long_term_direction"
                ? <textarea aria-label={goalCardFieldLabels[key]} rows={2} maxLength={maxLength[key]} value={draft[key]} onChange={event => change(key,event.target.value)} disabled={saving} placeholder="还没想好也没关系" className="archive-input w-full resize-y text-sm disabled:opacity-60" />
                : <input aria-label={goalCardFieldLabels[key]} type={numeric.has(key) ? "number" : "text"} inputMode={numeric.has(key) ? "decimal" : undefined} min={numeric.has(key) ? 0 : undefined} max={key === "difficulty_rating" ? 10 : key === "duration_minutes" ? 1440 : undefined} step={numeric.has(key) ? 1 : undefined} maxLength={maxLength[key]} value={draft[key]} onChange={event => change(key,event.target.value)} disabled={saving} placeholder={key === "schedule_text" ? "例如明天下午，或还没确定" : key === "duration_minutes" ? "分钟，先留空也可以" : "选填"} className="archive-input min-h-11 w-full text-sm disabled:opacity-60" />}
            </label>)}
          </div>
          {!!card.concerns?.length && <div className="mt-4 rounded-xl bg-raised p-3"><p className="text-xs font-medium text-accent-ink">还可以一起想想</p><ul className="mt-1 list-disc space-y-1 pl-4 text-xs leading-5 text-ink-muted">{card.concerns.map((concern,index) => <li key={index}>{concern}</li>)}</ul></div>}
          <div className="mt-5 flex flex-wrap gap-2"><button type="submit" disabled={lock} className="min-h-11 flex-1 rounded-xl bg-accent px-4 text-sm font-medium text-on-accent disabled:opacity-50 sm:flex-none">{saving ? "正在提交…" : "交给教练一起完善"}</button><button type="button" onClick={() => setExpanded(false)} className="min-h-11 rounded-xl px-3 text-sm text-ink-muted">先收起，继续聊天</button></div>
          {busy && <p className="mt-2 text-xs text-ink-muted">可以先填写，等教练回复后再提交。</p>}
        </form> : <><h3 className="text-base font-semibold">{card.phase === "paused" ? "暂存的" : "已确认的"}{kind}卡</h3><CardFields fields={card.fields} />{dirty && <details className="mt-4 rounded-xl bg-raised p-3"><summary className="cursor-pointer text-xs text-ink-muted">查看尚未提交的填写</summary><CardFields fields={fieldsOf(draft,card.kind)} /></details>}{card.phase === "paused" ? <><p className="mt-4 text-xs leading-5 text-ink-muted">先保留这些想法，需要时可以再和教练一起完善。</p><button type="button" disabled={lock} onClick={() => void resume()} className="mt-3 min-h-11 rounded-xl bg-accent px-4 text-sm text-on-accent disabled:opacity-50">继续完善</button></> : <p className="mt-4 text-xs leading-5 text-accent-ink">目标信息已保存到“我的目标”，可以随时在那里查看。</p>}</>}
      </div>}
      {card?.phase === "ready" && <div className="mt-3 rounded-xl border border-accent-edge bg-panel p-3">
        <p className="text-xs leading-5 text-ink-muted">教练已核对第 {card.revision} 版安排，请查看卡片后决定。{dirty ? "你还有未提交的修改，请先交给教练核对。" : "需要调整也可以继续聊天。"}</p>
        <div className="mt-2 flex flex-wrap gap-2"><button type="button" disabled={lock || dirty} onClick={() => void sendChoice("confirm")} className="min-h-10 rounded-xl bg-accent px-4 text-sm font-medium text-on-accent disabled:opacity-50">确认这个安排</button><button type="button" disabled={lock} onClick={() => setExpanded(true)} className="min-h-10 rounded-xl px-3 text-sm text-accent-ink">先查看或修改</button></div>
      </div>}
      {card && !["confirmed","paused"].includes(card.phase) && (expanded || card.phase === "ready") && <button type="button" disabled={lock} onClick={() => void sendChoice("pause")} className="mt-1 min-h-9 px-1 text-xs text-ink-muted underline decoration-line-strong underline-offset-4 disabled:opacity-50">暂不继续细化，保留草稿</button>}
    </div>
  </section>;
}

export function CardFields({fields}: {fields: GoalCardFields}) {
  return <dl className="mt-3 grid gap-3 sm:grid-cols-2">{keys.filter(key => fields[key] !== null && fields[key] !== undefined && fields[key] !== "").map(key => <div key={key} className="min-w-0"><dt className="text-xs text-ink-faint">{goalCardFieldLabels[key]}</dt><dd className="mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-ink">{goalCardFieldValue(key,fields[key])}</dd></div>)}</dl>;
}
