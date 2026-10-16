"use client";

import { useEffect, useState } from "react";
import ThemeToggle from "@/components/ThemeToggle";
import AuthEntryStyle from "@/components/AuthEntryStyle";
import { AuthError, verifyEmail } from "@/lib/auth";

export default function VerifyEmailPage() {
  const [state, setState] = useState<"checking" | "done" | "error">("checking");
  const [message, setMessage] = useState("正在验证邮箱…");

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token") ?? "";
    if (!token) {
      setState("error");
      setMessage("链接中没有验证凭证，请登录后重新发送验证邮件");
      return;
    }
    verifyEmail(token)
      .then((text) => {
        setState("done");
        setMessage(text);
      })
      .catch((err) => {
        setState("error");
        setMessage(err instanceof AuthError ? err.message : "邮箱验证失败");
      });
  }, []);

  return (
    <AuthEntryStyle>
    <main className="relative flex h-[100dvh] items-center justify-center overflow-clip bg-canvas p-4">
      <section className="zen-page-enter default-style:relative default-style:z-10 default-style:w-full default-style:max-w-md default-style:rounded-[28px] default-style:border default-style:border-line default-style:bg-panel default-style:p-7 default-style:text-center default-style:depth-panel default-style:backdrop-blur-2xl simple:relative simple:z-10 simple:w-full simple:max-w-md simple:rounded-2xl simple:border simple:border-line simple:bg-panel simple:p-7 simple:text-center simple:depth-panel ">
        <p className="text-[0.68rem] tracking-[0.18em] text-accent-ink">BA COACH · 邮箱验证</p>
        <div className={`mx-auto mt-5 h-3 w-3 rounded-full ${state === "error" ? "bg-alert-ink" : "bg-accent"} ${state === "checking" ? "zen-breathe" : ""}`} />
        <h1 className="mt-4 text-[1rem] text-ink">
          {state === "checking" ? "请稍候" : state === "done" ? "验证完成" : "无法验证"}
        </h1>
        <p className="mt-2 text-[0.78rem] leading-[1.8] text-ink-muted">{message}</p>
        {state !== "checking" && (
          <a href="https://bacoach.xyz/" className="mt-5 block rounded-2xl border border-accent-edge bg-accent-wash py-3 text-[0.82rem] text-accent-ink">
            返回 BA Coach
          </a>
        )}
      </section>
      <ThemeToggle />
    </main>
    </AuthEntryStyle>
  );
}
