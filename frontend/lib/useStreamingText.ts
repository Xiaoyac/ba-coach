"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { textCharacters } from "@/lib/textCharacters";
import { getLeadInterval } from "@/lib/leadPacing";

const LEAD_START_DELAY_MS = 1000;
const LEAD_MAX_INTERVAL_MS = 500;

/** Presentation only: received/saved text and the model stream are never delayed. */
export function useStreamingText(text: string, streaming: boolean,
  phase?: "lead" | "body", leadLength = 0) {
  const [visible, setVisible] = useState(streaming ? "" : text);
  const [animated, setAnimated] = useState(streaming);
  const displayed = useRef(visible);
  const started = useRef(streaming);
  const previous = useRef(text);
  const previousPhase = useRef(phase);
  const lastAdvance = useRef(0);
  const leadStarted = useRef<number | null>(null);
  const catchupUntil = useRef(0);
  const reduced = useRef(false);
  const flush = useRef(() => {});

  useLayoutEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => {
      reduced.current = preference.matches;
      if (preference.matches) flush.current();
    };
    update();
    preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);

  useLayoutEffect(() => {
    let frame = 0;
    let cancelled = false;
    const show = (value: string) => {
      displayed.current = value;
      setVisible(value);
    };
    const finish = () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      setAnimated(false);
      show(text);
    };
    flush.current = finish;
    if (streaming) started.current = true;
    const replaced = !text.startsWith(previous.current);
    previous.current = text;
    const leadWaiting = streaming && phase === "lead";
    if (leadWaiting && text && leadStarted.current === null) leadStarted.current = performance.now();
    if (phase === "body" && previousPhase.current === "lead") {
      // Body arrival ends slow lead pacing. Catch up within a quarter second;
      // do not add a long presentation queue in front of the real answer.
      catchupUntil.current = performance.now() + 240;
    }
    previousPhase.current = phase;

    // History, corrections, stopped leads and reduced motion are instant.
    // A normal body stream ending can still drain its queued characters.
    if (!started.current || reduced.current || replaced || document.hidden
      || (!streaming && phase === "lead")) {
      finish();
      return;
    }
    setAnimated(true);
    const remaining = textCharacters(text.slice(displayed.current.length));
    let index = 0;
    const tick = (now: number) => {
      if (cancelled) return;
      const left = remaining.length - index;
      if (!left) return;
      const leadAge = now - (leadStarted.current ?? now);
      const fixedInterval = getLeadInterval();
      // Hold only the lead's first character; incoming chunks share this clock.
      // A body/terminal event bypasses the delay and follows the normal handoff.
      // An admin's 0ms setting shows the lead as it arrives, without the hold.
      if (leadWaiting && fixedInterval !== 0 && leadAge < LEAD_START_DELAY_MS) {
        frame = requestAnimationFrame(tick);
        return;
      }
      // Reserve the closing character until the body or a terminal event.
      // Frequent one-character SSE packets must not reset the pacing clock.
      if (leadWaiting && left <= 1 && displayed.current.length > 0) return;
      const catchingLead = phase === "body" && displayed.current.length < leadLength;
      const elapsed = Math.max(0, leadAge - LEAD_START_DELAY_MS);
      // An admin's fixed interval replaces the default adaptive lead pacing.
      const interval = leadWaiting ? (fixedInterval ?? Math.min(LEAD_MAX_INTERVAL_MS, 280 + elapsed * .035))
        : catchingLead ? 12 : left > 60 ? 12 : 24;
      if (!displayed.current || now - lastAdvance.current >= interval) {
        let count = leadWaiting && fixedInterval === 0 ? Math.max(1, left - 1) : 1;
        if (catchingLead) {
          const leadLeft = textCharacters(text.slice(displayed.current.length, leadLength)).length;
          count = Math.max(1, Math.ceil(leadLeft / Math.max(1, (catchupUntil.current - now) / 16)));
        }
        show(displayed.current + remaining.slice(index, index + count).join(""));
        index += count;
        lastAdvance.current = now;
      }
      if (index < remaining.length) frame = requestAnimationFrame(tick);
    };
    const onVisibility = () => { if (document.hidden) finish(); };
    document.addEventListener("visibilitychange", onVisibility);
    if (remaining.length) frame = requestAnimationFrame(tick);
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [text, streaming, phase, leadLength]);

  return { text: visible, animated, revealing: visible !== text };
}
