"use client";

import Link from "next/link";
import { GearMark } from "@/components/icons";

export default function SettingsLink({ onClick, menu = false }: { onClick?: () => void; menu?: boolean }) {
  return <Link href="/settings" scroll={false} data-settings-trigger onClick={onClick} role={menu ? "menuitem" : undefined}
    className={`flex min-h-11 items-center gap-2 rounded-xl px-3 py-2 text-sm text-ink-muted hover:bg-raised hover:text-ink focus-visible:outline-2 focus-visible:outline-accent ${menu ? "w-full" : "shrink-0"}`}>
    <GearMark className="h-[18px] w-[18px] shrink-0" /><span>设置</span>
  </Link>;
}
