"use client";

import { useEffect, useMemo, useState } from "react";
import {
  fetchAdminPrompts,
  restorePromptVersion,
  resetAdminPrompt,
  saveAdminPrompt,
  type AdminPromptItem,
  type PromptKey,
} from "@/lib/adminPrompts";
import { CloseMark, PromptMark } from "@/components/icons";
import PromptHistoryPanel from "@/components/PromptHistoryPanel";
import ConfirmDialog from "@/components/ConfirmDialog";

type Confirmation = "discard" | "reset" | null;

export default function PromptManagerModal({ onClose }: { onClose: () => void }) {
  const [prompts, setPrompts] = useState<AdminPromptItem[]>([]);
  const [drafts, setDrafts] = useState<Partial<Record<PromptKey, string>>>({});
  const [selectedKey, setSelectedKey] = useState<PromptKey>("global");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedKey, setSavedKey] = useState<PromptKey | null>(null);
  const [view, setView] = useState<"edit" | "history">("edit");
  const [restoreTarget, setRestoreTarget] = useState<{key: PromptKey; revision: number} | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchAdminPrompts(controller.signal)
      .then((items) => {
        setPrompts(items);
        setDrafts(Object.fromEntries(items.map((item) => [item.key, item.content])));
      })
      .catch((err) => {
        if (err?.name !== "AbortError") setError(String(err?.message ?? err));
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const selected = prompts.find((item) => item.key === selectedKey) ?? null;
  const draft = drafts[selectedKey] ?? "";
  const dirtyKeys = useMemo(
    () =>
      prompts
        .filter((item) => drafts[item.key] !== item.content)
        .map((item) => item.key),
    [drafts, prompts],
  );
  const dirty = dirtyKeys.includes(selectedKey);

  function requestClose() {
    if (saving) return;
    if (dirtyKeys.length > 0) {
      setConfirmation("discard");
      return;
    }
    onClose();
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !confirmation && !restoreTarget) requestClose();
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (view === "edit" && dirty && !saving && !restoreTarget && !confirmation) void handleSave();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });

  function replacePrompt(updated: AdminPromptItem) {
    setPrompts((prev) =>
      prev.map((item) => (item.key === updated.key ? updated : item)),
    );
    setDrafts((prev) => ({ ...prev, [updated.key]: updated.content }));
  }

  async function handleSave() {
    if (!selected || !draft.trim() || saving) return;
    setSaving(true);
    setError(null);
    setSavedKey(null);
    try {
      const updated = await saveAdminPrompt(selected.key, draft, selected.revision);
      replacePrompt(updated);
      setSavedKey(updated.key);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    if (!selected || saving) return;
    setConfirmation("reset");
  }

  async function performReset() {
    if (!selected || saving) return;
    setConfirmation(null);
    setSaving(true);
    setError(null);
    setSavedKey(null);
    try {
      const updated = await resetAdminPrompt(selected.key, selected.revision);
      replacePrompt(updated);
      setSavedKey(updated.key);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function reloadKeepingDraft() {
    setSaving(true);
    try { setPrompts(await fetchAdminPrompts()); setError("已载入最新版本，未保存草稿仍在编辑框中。请核对版本历史后再保存。"); }
    catch (err) {setError(err instanceof Error ? err.message : String(err));}
    finally {setSaving(false);}
  }

  async function performRestore() {
    if (!restoreTarget || saving) return;
    const current = prompts.find(p => p.key === restoreTarget.key);
    if (!current) return;
    setSaving(true);setError(null);
    try {
      const updated = await restorePromptVersion(current.key,restoreTarget.revision,current.revision);
      replacePrompt(updated);setSavedKey(updated.key);setRestoreTarget(null);
    } catch(err) {setRestoreTarget(null);setError(err instanceof Error ? err.message : String(err));}
    finally {setSaving(false);}
  }

  return (
    <div className="zen-overlay-enter fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-5">
      <button
        type="button"
        aria-label="关闭提示词管理"
        onClick={requestClose}
        className="default-style:absolute default-style:inset-0 default-style:bg-canvas/75 default-style:backdrop-blur-md simple:absolute simple:inset-0 simple:bg-canvas/75 simple:backdrop-blur-sm"
      />

      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="prompt-manager-title"
        className="default-style:relative default-style:flex default-style:h-[min(880px,calc(100dvh-1.5rem))] default-style:min-h-0 default-style:w-full default-style:max-w-6xl default-style:flex-col default-style:overflow-hidden default-style:rounded-[28px] default-style:border default-style:border-line default-style:bg-panel default-style:depth-panel default-style:backdrop-blur-2xl default-style:sm:h-[min(880px,calc(100dvh-2.5rem))] simple:relative simple:flex simple:h-[min(880px,calc(100dvh-1.5rem))] simple:min-h-0 simple:w-full simple:max-w-6xl simple:flex-col simple:overflow-hidden simple:rounded-2xl simple:border simple:border-line simple:bg-panel simple:depth-panel simple:sm:h-[min(880px,calc(100dvh-2.5rem))]"
      >
        <header className="flex shrink-0 items-start gap-3 border-b border-line px-5 py-4 sm:px-6">
          <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-accent-edge bg-accent-wash text-accent-ink">
            <PromptMark className="h-4.5 w-4.5" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 id="prompt-manager-title" className="text-base font-medium text-ink">
                管理员 · 全局提示词管理
              </h2>
              <span className="rounded-full bg-accent-wash px-2 py-0.5 text-[0.62rem] tracking-[0.12em] text-accent-ink">
                ADMIN
              </span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-ink-faint">
              系统级共享配置，所有管理员编辑同一份内容；保存后会影响所有用户的下一轮对话。
            </p>
            <p className="mt-1 text-xs leading-relaxed text-ink-faint">
              这里编辑的是模型理解层；服务器的流程状态、确认门禁、确定性卡片和回复完整性校验仍会优先生效。
            </p>
          </div>
          <button
            type="button"
            onClick={requestClose}
            aria-label="关闭"
            className="ml-auto grid h-9 w-9 shrink-0 place-items-center rounded-full text-ink-muted transition-colors duration-300 hover:bg-raised hover:text-ink"
          >
            <CloseMark className="h-4 w-4" />
          </button>
        </header>

        {loading ? (
          <div className="grid min-h-0 flex-1 place-items-center" aria-busy="true">
            <span className="h-7 w-7 animate-spin rounded-full border-2 border-line-strong border-t-accent" />
          </div>
        ) : error && prompts.length === 0 ? (
          <div className="m-auto max-w-md rounded-2xl border border-alert-edge bg-alert-wash p-5 text-sm leading-relaxed text-alert-ink">
            {error}
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col md:flex-row">
            <nav
              aria-label="提示词列表"
              className="zen-scroll flex shrink-0 gap-2 overflow-x-auto border-b border-line p-3 md:w-60 md:flex-col md:overflow-y-auto md:border-b-0 md:border-r"
            >
              {prompts.map((item) => {
                const itemDirty = drafts[item.key] !== item.content;
                return (
                  <button
                    key={item.key}
                    disabled={saving}
                    type="button"
                    onClick={() => {
                      setSelectedKey(item.key);
                      setRestoreTarget(null);
                      setSavedKey(null);
                      setError(null);
                    }}
                    className={`min-w-[9rem] rounded-2xl border px-3.5 py-3 text-left transition-all duration-300 md:min-w-0 ${
                      selectedKey === item.key
                        ? "border-accent-edge bg-accent-wash text-ink"
                        : "border-transparent text-ink-muted hover:border-line hover:bg-raised hover:text-ink"
                    }`}
                  >
                    <span className="flex items-center justify-between gap-2 text-xs font-medium tracking-[0.06em]">
                      {item.label}
                      {itemDirty ? (
                        <span className="h-1.5 w-1.5 rounded-full bg-accent" title="尚未保存" />
                      ) : item.is_overridden ? (
                        <span className="text-[0.58rem] font-normal tracking-normal text-accent-ink">
                          已自定义
                        </span>
                      ) : null}
                    </span>
                    <span className="mt-1.5 hidden text-[0.68rem] leading-relaxed text-ink-faint md:block">
                      {item.description}
                    </span>
                  </button>
                );
              })}
            </nav>

            {selected && (
              <div className="flex min-h-0 flex-1 flex-col p-4 sm:p-5">
                <div className="mb-3 flex shrink-0 items-end justify-between gap-4">
                  <div>
                    <h3 className="text-sm font-medium text-ink">{selected.label}</h3>
                    <p className="mt-1 text-xs text-ink-faint">{selected.description}</p>
                  </div>
                  <span className="shrink-0 text-[0.68rem] tabular-nums text-ink-faint">
                    {draft.length.toLocaleString()} 字符
                  </span>
                </div>

                <div className="mb-3 flex shrink-0 gap-2" role="tablist" aria-label="提示词编辑与历史">
                  {(["edit","history"] as const).map(tab=><button key={tab} type="button" role="tab" aria-selected={view===tab} onClick={()=>setView(tab)} className={`rounded-lg px-3 py-2 text-xs ${view===tab ? "bg-accent-wash text-accent-ink" : "text-ink-muted hover:bg-raised"}`}>{tab==="edit" ? "编辑内容" : "版本历史与对比"}</button>)}
                </div>
                {view === "history" && <PromptHistoryPanel key={selected.key} promptKey={selected.key} currentRevision={selected.revision} busy={saving} onRestore={revision=>setRestoreTarget({key:selected.key,revision})} />}
                <textarea
                  hidden={view !== "edit"}
                  aria-label={`${selected.label}内容`}
                  value={draft}
                  onChange={(event) => {
                    setDrafts((prev) => ({
                      ...prev,
                      [selected.key]: event.target.value,
                    }));
                    setSavedKey(null);
                  }}
                  spellCheck={false}
                  className="zen-scroll min-h-0 flex-1 resize-none rounded-2xl border border-line bg-canvas/45 px-4 py-3 font-mono text-[0.8rem] leading-7 text-ink outline-none transition-colors duration-300 placeholder:text-ink-faint focus:border-accent-edge focus:bg-canvas/65"
                />

                {error && (
                  <p className="mt-3 rounded-xl border border-alert-edge bg-alert-wash px-3 py-2 text-xs text-alert-ink">
                    {error}
                    <button type="button" disabled={saving} onClick={()=>void reloadKeepingDraft()} className="ml-3 underline">载入最新版本（保留草稿）</button>
                  </p>
                )}

                <footer className={`${view === "edit" ? "flex" : "hidden"} mt-3 shrink-0 flex-wrap items-center gap-2`}>
                  <div className="mr-auto min-h-5 text-[0.68rem] text-ink-faint">
                    {savedKey === selected.key
                      ? "已保存，所有用户的下一轮对话将使用此版本。"
                      : selected.is_overridden && selected.updated_at
                        ? `由 ${selected.updated_by ?? "管理员"} 更新于 ${new Date(selected.updated_at).toLocaleString("zh-CN")}`
                        : "当前使用源码默认版本。"}
                  </div>
                  <button
                    type="button"
                    onClick={handleReset}
                    disabled={saving || (!selected.is_overridden && !dirty)}
                    className="rounded-full border border-line px-4 py-2 text-xs text-ink-muted transition-colors duration-300 hover:border-line-strong hover:text-ink disabled:cursor-not-allowed disabled:opacity-35"
                  >
                    恢复默认
                  </button>
                  <button
                    type="button"
                    onClick={handleSave}
                    disabled={saving || !dirty || !draft.trim()}
                    className="min-w-24 rounded-full border border-accent-edge bg-accent-wash px-4 py-2 text-xs font-medium text-accent-ink transition-all duration-300 hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-35"
                  >
                    {saving ? "保存中…" : "保存修改"}
                  </button>
                </footer>
              </div>
            )}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={restoreTarget !== null}
        title={`恢复到版本 ${restoreTarget?.revision ?? ""}？`}
        description="此版本的完整文字将作为新版本保存，并影响后续对话。现有历史会保留；当前提示词未保存的草稿会被替换。"
        confirmLabel="恢复并生成新版本" cancelLabel="取消" tone="alert" busy={saving}
        onCancel={()=>{if (!saving) setRestoreTarget(null);}} onConfirm={()=>void performRestore()}
      />
      <ConfirmDialog
        open={confirmation === "discard"}
        title="放弃尚未保存的修改？"
        description={`还有 ${dirtyKeys.length} 项提示词修改尚未保存。离开后，这些修改将不会生效。`}
        confirmLabel="放弃修改"
        cancelLabel="继续编辑"
        tone="alert"
        onCancel={() => setConfirmation(null)}
        onConfirm={onClose}
      />
      <ConfirmDialog
        open={confirmation === "reset"}
        title="恢复源码默认内容？"
        description={`「${selected?.label ?? "当前提示词"}」的自定义内容将被默认版本取代。`}
        confirmLabel="恢复默认"
        cancelLabel="保留当前内容"
        tone="alert"
        busy={saving}
        onCancel={() => setConfirmation(null)}
        onConfirm={() => void performReset()}
      />
    </div>
  );
}
