"use client";

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { ChevronDownMark } from "@/components/icons";

type Option = { value: string; label: string };

/** A themed select-only combobox. The popover stays inside its owning dialog
 * for accessibility/theme inheritance, but uses the top layer to avoid clipping. */
export default function ArchiveSelect({ label, value, options, onChange, className = "" }: {
  label: string; value: string; options: Option[]; onChange: (value: string) => void; className?: string;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState({ left: 0, top: 0, width: 208, maxHeight: 280 });
  const selected = options.findIndex(option => option.value === value);
  const search = useRef({ text: "", time: 0 });

  function reposition() {
    if (!trigger.current) return;
    const box = trigger.current.getBoundingClientRect();
    const dialog = trigger.current.closest("dialog")?.getBoundingClientRect();
    const leftEdge = Math.max(8, (dialog?.left ?? 0) + 8);
    const rightEdge = Math.min(window.innerWidth - 8, (dialog?.right ?? window.innerWidth) - 8);
    const width = Math.min(Math.max(box.width, 208), rightEdge - leftEdge);
    const desiredHeight = Math.min(options.length * 44 + 12, 280);
    const below = window.innerHeight - box.bottom - 14;
    const above = box.top - 14;
    const upward = below < desiredHeight && above > below;
    const maxHeight = Math.max(44, Math.min(desiredHeight, upward ? above : below));
    setPosition({
      width, maxHeight,
      left: Math.max(leftEdge, Math.min(box.left + width > rightEdge ? box.right - width : box.left, rightEdge - width)),
      top: upward ? Math.max(8, box.top - maxHeight - 6) : box.bottom + 6,
    });
  }

  function close(restoreFocus = false) {
    if (menu.current?.matches(":popover-open")) menu.current.hidePopover();
    setOpen(false);
    if (restoreFocus) trigger.current?.focus({ preventScroll: true });
  }

  function show(index = Math.max(0, selected)) {
    reposition();
    setActive(index);
    search.current = { text: "", time: 0 };
    menu.current?.showPopover();
    setOpen(true);
  }

  function choose(index: number) {
    if (options[index]) onChange(options[index].value);
    close(true);
  }

  useEffect(() => {
    const panel = menu.current;
    const sync = (event: Event) => setOpen((event as ToggleEvent).newState === "open");
    panel?.addEventListener("beforetoggle", sync);
    return () => panel?.removeEventListener("beforetoggle", sync);
  }, []);

  useEffect(() => {
    if (!open) return;
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, options.length]);

  useEffect(() => {
    if (!open || !menu.current) return;
    // Scroll the menu only, not the underlying modal or page.
    const option = menu.current.querySelector<HTMLElement>(`[data-option-index="${active}"]`);
    if (option) {
      if (option.offsetTop < menu.current.scrollTop) menu.current.scrollTop = option.offsetTop;
      else if (option.offsetTop + option.offsetHeight > menu.current.scrollTop + menu.current.clientHeight)
        menu.current.scrollTop = option.offsetTop + option.offsetHeight - menu.current.clientHeight;
    }
  }, [active, open]);

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Tab") { close(); return; }
    if (event.key === "Escape" && open) {
      event.preventDefault(); event.stopPropagation(); close(true); return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End", "Enter", " "].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      if (!open) { show(event.key === "End" ? options.length - 1 : event.key === "Home" ? 0 : Math.max(0, selected)); return; }
      if (event.key === "Enter" || event.key === " ") { choose(active); return; }
      setActive(current => event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 :
        (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length);
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && !event.nativeEvent.isComposing) {
      const now = Date.now();
      const text = (now - search.current.time < 700 ? search.current.text : "") + event.key;
      const index = options.findIndex(option => option.label.toLocaleLowerCase().startsWith(text.toLocaleLowerCase()));
      if (index >= 0) { event.preventDefault(); if (!open) show(index); else setActive(index); }
      search.current = { text, time: now };
    }
  }

  return <div className={`relative min-w-0 ${className}`}>
    <button ref={trigger} type="button" role="combobox" aria-label={label} aria-haspopup="listbox"
      aria-controls={id} aria-expanded={open} aria-activedescendant={open ? `${id}-${active}` : undefined}
      onKeyDown={onKeyDown} onClick={() => open ? close() : show()}
      className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-xl border bg-raised px-3 py-2.5 text-left text-sm text-ink transition-colors hover:border-accent-edge focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${open ? "border-accent-edge" : "border-line"}`}>
      <span className="truncate">{options[selected]?.label ?? "请选择"}</span>
      <ChevronDownMark aria-hidden className={`size-3.5 shrink-0 text-accent-ink transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`} />
    </button>
    <div ref={menu} id={id} popover="auto" role="listbox" aria-label={label} onKeyDown={onKeyDown}
      style={position} className="zen-scroll fixed m-0 overflow-y-auto overscroll-contain rounded-2xl border border-accent-edge bg-sheet p-1.5 text-ink shadow-xl">
      {options.map((option, index) => <button key={option.value} id={`${id}-${index}`} type="button"
        role="option" aria-selected={value === option.value} tabIndex={-1} data-option-index={index} data-value={option.value}
        onMouseDown={event => event.preventDefault()} onPointerMove={() => setActive(index)} onClick={() => choose(index)}
        className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-sm transition-colors ${active === index ? "bg-accent-wash text-accent-ink" : "text-ink-muted hover:bg-raised"} ${value === option.value ? "font-medium" : ""}`}>
        <span>{option.label}</span><span aria-hidden="true" className="w-4 shrink-0 text-accent-ink">{value === option.value ? "✓" : ""}</span>
      </button>)}
    </div>
  </div>;
}
