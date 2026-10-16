"use client";
import { useEffect, useState } from "react";
import MessageRow from "@/components/MessageRow";
const demo = "可以从你觉得轻松的一小步开始。\n\n比如今天饭后，去附近走一走，留意身体和心情有没有变化。时间由你决定，不必一次做很多。🌿\n\n**我们慢慢来。** 你觉得什么时候比较方便？";
export default function Preview() {
  const [text, setText] = useState("");
  const [pending, setPending] = useState(true);
  const [room, setRoom] = useState(0);
  const [phase, setPhase] = useState<"lead" | "body" | undefined>();
  const [leadLength, setLeadLength] = useState(0);
  const [startedAt, setStartedAt] = useState(Date.now());
  useEffect(() => {
    (window as any).revealFixture = {
      update: (content: string, busy: boolean, stage?: "lead" | "body", length = 0) => {
        setText(content); setPending(busy); setPhase(stage); setLeadLength(length);
      },
      reset: (content = "", busy = true) => {
        setText(content); setPending(busy); setPhase(undefined); setLeadLength(0);
        setStartedAt(Date.now()); setRoom(n => n + 1);
      },
    };
    const timer = setTimeout(() => { setText(demo); setPending(false); }, 800);
    return () => clearTimeout(timer);
  }, []);
  return <main className="min-h-screen bg-canvas px-5 py-12 text-ink">
    <div className="mx-auto mb-8 max-w-3xl"><p className="text-sm text-ink-muted">本地效果预览</p><h1 className="my-3 text-2xl">逐字浮现</h1><p className="text-sm text-ink-muted">使用实际对话组件，模拟整段文字一次到达。</p><button className="mt-5 rounded-full border border-line-strong px-5 py-2" onClick={() => {
      setText(""); setPending(true); setRoom(n => n + 1);
      setTimeout(() => { setText(demo); setPending(false); }, 300);
    }}>重播动画</button></div>
    <section key={room} data-fixture-room={room}><MessageRow message={{role:"assistant", content:text,
      stream_phase:phase,stream_lead_length:leadLength}} pending={pending} generationStartedAt={startedAt}
      routingPending={false} animate={false} showDiagnostics={false}/></section>
  </main>;
}
