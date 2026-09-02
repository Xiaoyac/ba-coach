"use client";

import { useEffect, useState } from "react";
import { ChevronDownMark, SandboxMark } from "@/components/icons";
import type { SandboxModule } from "@/lib/adminSandbox";

const MODULES: Array<{
  id: SandboxModule;
  label: string;
  description: string;
}> = [
  { id: "module_1", label: "MODULE I", description: "困扰探索与 BA 教育" },
  { id: "module_2", label: "MODULE II", description: "目标设定与 PA 卡片" },
  { id: "module_3", label: "MODULE III", description: "行动契约与执行支持" },
  { id: "module_4", label: "MODULE IV", description: "复盘、调整与巩固" },
];

export default function AdminSandboxControl({
  busy,
  onStart,
}: {
  busy: boolean;
  onStart: (module: SandboxModule) => void;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    function closeOnOutside(event: PointerEvent) {
      const target = event.target as HTMLElement | null;
      if (!target?.closest("[data-admin-sandbox]")) setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className="relative" data-admin-sandbox>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={busy}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-2xl border border-accent-edge bg-accent-wash px-3.5 py-2.5 text-left text-[0.78rem] font-medium text-accent-ink transition-all duration-300 hover:bg-accent-soft disabled:cursor-wait disabled:opacity-55"
      >
        {busy ? (
          <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-line-strong border-t-accent motion-reduce:animate-none" />
        ) : (
          <SandboxMark className="h-4 w-4 shrink-0" />
        )}
        <span className="min-w-0 flex-1 truncate">沙盒模块</span>
        <span className="rounded-full border border-accent-edge px-1.5 py-0.5 text-[0.52rem] tracking-[0.12em]">
          ADMIN
        </span>
        <ChevronDownMark
          className={`h-3.5 w-3.5 shrink-0 transition-transform duration-300 ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="选择沙盒起始模块"
          className="absolute left-0 top-full z-50 mt-2 w-full overflow-hidden rounded-2xl border border-accent-edge bg-panel/95 p-1.5 depth-panel backdrop-blur-2xl"
        >
          <p className="px-2.5 pb-2 pt-1.5 text-[0.62rem] leading-relaxed text-ink-faint">
            每次选择都会创建全新空白对话，不写入真实临床进度。
          </p>
          {MODULES.map((module) => (
            <button
              key={module.id}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onStart(module.id);
              }}
              className="flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left text-ink-muted transition-colors duration-200 hover:bg-accent-wash hover:text-accent-ink"
            >
              <span className="w-[4.9rem] shrink-0 text-[0.7rem] font-medium tracking-[0.08em]">
                {module.label}
              </span>
              <span className="truncate text-[0.65rem] text-ink-faint">
                {module.description}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
