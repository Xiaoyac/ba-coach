"use client";
import { useEffect, useRef, useState } from "react";
import ExecutionDrawer from "@/components/ExecutionDrawer";
import MessageRow from "@/components/MessageRow";
import ThemeToggle from "@/components/ThemeToggle";
import { beginResponseTiming, bindResponseTiming, noteBodyOffset, endResponseTiming } from "@/lib/responseTiming";
import type { ChatMessage } from "@/lib/api";
const sid="drawer-preview";
export default function Preview() {
 const [ready,setReady]=useState(false),[busy,setBusy]=useState(false),[message,setMessage]=useState<ChatMessage>({role:"assistant",content:"点击“模拟发送”，体验从发送到正文首字的计时。"});
 const serial=useRef(20),timers=useRef<ReturnType<typeof setTimeout>[]>([]);
 const demo=useRef({done:true,turns:[{id:20,preview:"今天想从一件轻松的小事开始",created_at:"2026-10-15T08:30:00Z"},{id:19,preview:"昨天散步后，心情放松了一点",created_at:"2026-10-15T08:10:00Z"},{id:18,preview:"最近工作比较累，想找回自己的节奏",created_at:"2026-10-14T09:00:00Z"}],times:{20:2180,19:3420} as Record<number,number>});
 useEffect(()=>{
  const original=window.fetch;
  window.fetch=async(input,options={})=>{
   const url=new URL(typeof input==="string"?input:input instanceof URL?input.href:input.url,location.href);
   if(url.pathname===`/api/conversations/${sid}/execution/client-timing`){const p=JSON.parse(String(options.body));demo.current.times[p.user_message_id]=p.first_body_ms;return Response.json({first_body_ms:p.first_body_ms});}
   if(url.pathname===`/api/conversations/${sid}/execution/history`){const d=demo.current,before=Number(url.searchParams.get("before_id")||d.turns[0].id);return Response.json({session_id:sid,current_user_message_id:d.turns[0].id,turns:d.turns.filter(t=>t.id<before),next_before_id:null});}
   if(url.pathname===`/api/conversations/${sid}/execution`){const d=demo.current,id=Number(url.searchParams.get("user_message_id")||d.turns[0].id),done=d.done||id!==d.turns[0].id;return Response.json({session_id:sid,user_message_id:id,module:"module_1",live:!done,request_finished:done,state:done?"completed":"running",client_first_body_ms:d.times[id]??null,turns:d.turns,server_time:Date.now()/1000,stages:[{stage:"risk_gate",label:"风险判断",lane:"foreground",status:"completed",model:"deepseek-v4.1-flash",duration_ms:320,calls:1},{stage:"main_generation",label:"正文生成",lane:"foreground",status:done?"completed":"running",model:"kimi-k3",duration_ms:done?1100:null,elapsed_ms:900,calls:1}]});}
   return original(input,options);
  };
  setReady(true);return()=>{window.fetch=original;timers.current.forEach(clearTimeout);};
 },[]);
 const send=()=>{
  const id=++serial.current,generation=crypto.randomUUID();beginResponseTiming(generation,sid,performance.now());bindResponseTiming(generation,id);
  const lead="听到了，我们可以慢慢聊。",body="先从轻松的一小步开始，选择自己觉得舒服的节奏。";
  demo.current.done=false;demo.current.turns.unshift({id,preview:`演示第 ${id-20} 轮：从轻松的一步开始`,created_at:"2026-10-15"});
  setBusy(true);setMessage({role:"assistant",content:"",reply_to_message_id:id});
  timers.current.push(setTimeout(()=>setMessage({role:"assistant",reply_to_message_id:id,content:lead,stream_phase:"lead",stream_lead_length:lead.length}),200));
  timers.current.push(setTimeout(()=>{noteBodyOffset(generation,lead.length);setMessage({role:"assistant",reply_to_message_id:id,content:lead+"\n\n"+body,stream_phase:"body",stream_lead_length:lead.length});},1200));
  timers.current.push(setTimeout(()=>{demo.current.done=true;endResponseTiming(generation);setBusy(false);},2400));
 };
 return <main className="min-h-screen bg-canvas p-6 text-ink sm:p-10"><div className="max-w-xl space-y-5"><p className="text-xs text-ink-muted">本地预览 · 模型阶段为演示数据，首字耗时由浏览器实际计时</p><h1 className="text-2xl font-medium">当前流程与历史运行记录</h1><ThemeToggle embedded/>
 <div className="flex gap-4"><button type="button" disabled={busy} onClick={send} className="rounded-xl bg-accent px-5 py-2 text-on-accent disabled:opacity-40">模拟发送</button>{ready&&<ExecutionDrawer sessionId={sid} busy={busy}/>}</div>
 {ready&&<MessageRow key={serial.current} sessionId={sid} message={message} pending={busy} routingPending={false} animate={false} showDiagnostics={false}/>}</div></main>;
}
