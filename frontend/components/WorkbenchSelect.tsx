import type { SelectHTMLAttributes } from "react";
import { ChevronDownMark } from "@/components/icons";

/** Keep native keyboard/mobile selection while giving every picker one visual language. */
export default function WorkbenchSelect({ className = "", children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <span className={`relative inline-flex min-w-0 ${className}`}>
    <select {...props} className="premium-control w-full cursor-pointer appearance-none rounded-xl border border-line-strong bg-raised/70 py-2.5 pl-3 pr-9 text-sm text-ink outline-none transition focus:border-accent-edge focus:ring-4 focus:ring-accent-wash disabled:cursor-not-allowed disabled:opacity-40">{children}</select>
    <ChevronDownMark aria-hidden className="pointer-events-none absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-faint" />
  </span>;
}
