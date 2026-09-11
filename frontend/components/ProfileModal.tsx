"use client";

import { useEffect, useState } from "react";
import {
  ACTIVITY_ENVIRONMENT,
  ACTIVITY_INTENSITY,
  ACTIVITY_SOCIAL,
  BEHAVIOR_TABOO,
  COMMUNICATION_PREFERENCE,
  CONTENT_TABOO,
  LIVING_STATUS,
  PHYSICAL_CONDITION,
  REMINDER_FREQUENCY,
  REMINDER_TIME_SLOT,
  SUPPORTER_INFLUENCE,
  SUPPORTER_RELATION,
  fetchProfile,
  updateProfile,
  type Profile,
  type ProfilePatch,
  type ModelProvider,
  type Supporter,
} from "@/lib/profile";
import { CloseMark } from "@/components/icons";

/**
 * The subject's own profile, editable.
 *
 * Everything here is sent to the coach on every turn (see the backend's
 * `load_profile_context`), which is why the copy says so plainly: someone
 * should be able to predict what changing a field will do. The two safety
 * sections are labelled as such rather than left to look like preferences.
 *
 * Saves the *difference*, not the whole object — see `ProfilePatch`. A form
 * that PUT its entire state back would revert anything changed on another
 * device between load and save.
 */
export default function ProfileModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved?: () => void;
}) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [draft, setDraft] = useState<ProfilePatch>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    // `cancelled` rather than only aborting: `finally` runs on an *aborted*
    // request too, and React's development double-mount aborts the first one.
    // That flipped `loading` to false while `profile` was still null, so the
    // "读不到档案" branch painted for a moment before the second attempt
    // resolved — which reads as a failure, not as loading.
    let cancelled = false;
    setLoading(true);
    setError(null);
    const timeout = window.setTimeout(() => controller.abort(), 15000);

    fetchProfile(controller.signal)
      .then((loaded) => {
        if (cancelled) return;
        setProfile(loaded);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err?.name === "AbortError" ? "档案加载超时，请检查网络后重新加载。" : String(err?.message ?? err));
        setLoading(false);
      }).finally(() => window.clearTimeout(timeout));

    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [loadAttempt]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  /** Current value: the pending edit if there is one, else what was loaded. */
  function value<K extends keyof ProfilePatch>(key: K): Profile[K] {
    return (key in draft ? draft[key] : profile?.[key]) as Profile[K];
  }

  function set<K extends keyof ProfilePatch>(key: K, next: Profile[K]) {
    setDraft((prev) => ({ ...prev, [key]: next }));
    setSaved(false);
  }

  function toggle(
    key: "physical_condition" | "behavior_taboo" | "content_taboo",
    option: string,
  ) {
    // Computed *inside* the updater, not from `value(key)`. React batches
    // state updates, so two toggles in the same tick both read the same
    // pre-render `draft` — the second would be built from the stale list and
    // silently undo the first. Only `prev` is guaranteed current.
    setDraft((prev) => {
      const current =
        ((key in prev ? prev[key] : profile?.[key]) as string[] | null) ?? [];
      return {
        ...prev,
        [key]: current.includes(option)
          ? current.filter((v) => v !== option)
          : [...current, option],
      };
    });
    setSaved(false);
  }

  // --- Supporters: an arbitrary-length list, added and removed here -------
  const supporters = (value("supporters") as Supporter[] | null) ?? [];

  function writeSupporters(next: Supporter[]) {
    set("supporters", next);
    // The list is what "do you have someone" actually means, so keep the flag
    // consistent rather than letting them disagree.
    set("has_supporter", next.length > 0);
  }

  function addSupporter() {
    writeSupporters([...supporters, { relation: "", nickname: null, influence: null }]);
  }

  function updateSupporter(index: number, next: Supporter) {
    writeSupporters(supporters.map((s, i) => (i === index ? next : s)));
  }

  function removeSupporter(index: number) {
    writeSupporters(supporters.filter((_, i) => i !== index));
  }

  async function handleSave() {
    if (saving || Object.keys(draft).length === 0) return;
    if ("tag" in draft && !/^[0-9]{5}$/.test(draft.tag ?? "")) {
      setError("标签必须是五位数字");
      return;
    }
    if ("nickname" in draft && (!draft.nickname?.trim() || draft.nickname.includes("#"))) {
      setError("昵称不能为空，也不能包含 #");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await updateProfile(draft);
      setProfile(updated);
      setDraft({});
      setSaved(true);
      onSaved?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const dirty = Object.keys(draft).length > 0;

  return (
    <div className="zen-overlay-enter fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4">
      <div
        aria-hidden
        onClick={onClose}
        className="fixed inset-0 bg-canvas/70 backdrop-blur-sm"
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-label="我的档案"
        className="relative my-auto w-full max-w-lg rounded-[28px] border border-line bg-panel p-6 depth-panel backdrop-blur-2xl"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          className="absolute right-4 top-4 flex h-8 w-8 items-center justify-center rounded-full text-ink-faint transition-colors duration-300 hover:bg-raised hover:text-ink-muted"
        >
          <CloseMark className="h-4 w-4" />
        </button>

        <h2 className="text-[0.95rem] text-ink">我的档案</h2>
        <p className="mt-1.5 mb-5 text-[0.72rem] leading-relaxed text-ink-faint">
          这些内容每一轮对话都会告诉教练。改完点保存，下一句话就会按新的来。
        </p>

        {loading ? (
          <div className="space-y-2 py-6" aria-busy="true">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-10 animate-pulse rounded-xl bg-raised" />
            ))}
          </div>
        ) : !profile ? (
          <div role="alert" className="rounded-xl bg-alert-wash px-3 py-2.5 text-[0.78rem] text-alert-ink">
            <p>{error ?? "暂时无法读取档案"}</p>
            <button className="mt-3 rounded-lg border border-line px-3 py-2" onClick={() => setLoadAttempt(n => n + 1)}>重新加载档案</button>
          </div>
        ) : (
          <>
            <Section title="基本信息">
              <div>
                <p className="mb-2 text-[0.68rem] leading-relaxed text-ink-faint">
                  公开身份由昵称与五位标签共同组成；昵称可以重复，但组合必须唯一。
                </p>
                <div className="grid grid-cols-[minmax(0,1.2fr)_minmax(7.25rem,0.8fr)] gap-3">
                  <label className="rounded-xl border border-line bg-raised px-3 py-2.5 transition-colors duration-300 focus-within:border-accent-edge focus-within:bg-panel">
                    <span className="block text-[0.61rem] font-medium tracking-[0.12em] text-ink-faint">
                      公开昵称
                    </span>
                    <input
                      aria-label="公开昵称"
                      value={(value("nickname") as string) ?? ""}
                      onChange={(e) => set("nickname", e.target.value || null)}
                      maxLength={58}
                      className="mt-1 w-full min-w-0 bg-transparent text-[0.92rem] text-ink outline-none placeholder:text-ink-faint"
                    />
                  </label>
                  <label className="rounded-xl border border-line bg-raised px-3 py-2.5 transition-colors duration-300 focus-within:border-accent-edge focus-within:bg-panel">
                    <span className="block text-[0.61rem] font-medium tracking-[0.12em] text-ink-faint">
                      五位标签
                    </span>
                    <span className="mt-1 flex items-center gap-1.5">
                      <span className="font-mono text-[0.92rem] text-ink-faint">#</span>
                      <input
                        aria-label="五位数字标签"
                        value={(value("tag") as string) ?? ""}
                        onChange={(e) =>
                          set("tag", e.target.value.replace(/\D/g, "").slice(0, 5) || null)
                        }
                        inputMode="numeric"
                        pattern="[0-9]{5}"
                        minLength={5}
                        maxLength={5}
                        placeholder="12345"
                        className="w-full min-w-0 bg-transparent font-mono text-[0.92rem] tracking-[0.1em] text-ink outline-none placeholder:tracking-normal placeholder:text-ink-faint"
                      />
                    </span>
                  </label>
                </div>
                <p className="mt-2 text-[0.68rem] text-ink-faint">
                  对外显示：{profile.display_id ?? "尚未设置标签"}
                </p>
              </div>
              <Field label="年龄">
                <input
                  type="number"
                  min={10}
                  max={120}
                  value={(value("age") as number) ?? ""}
                  onChange={(e) =>
                    set("age", e.target.value === "" ? null : Number(e.target.value))
                  }
                  className={inputClass}
                />
              </Field>
              <Field label="居住状况">
                <Choices
                  options={LIVING_STATUS}
                  selected={value("living_status") as string | null}
                  onSelect={(v) => set("living_status", v)}
                />
              </Field>
            </Section>

            <Section
              title="模型偏好"
              hint="选择接下来由哪个模型回复。这个入口是项目阶段性设置，之后可以直接移除，不影响账号和对话记录。"
            >
              <div role="radiogroup" aria-label="对话模型" className="grid grid-cols-2 gap-2">
                {(
                  [
                    ["deepseek", "DeepSeek", "当前主要模型"],
                    ["doubao", "豆包 Doubao", "火山方舟"],
                  ] as const
                ).map(([provider, label, description]) => {
                  const available = profile.available_providers[provider];
                  const selected = value("preferred_provider") === provider;
                  return (
                    <button
                      key={provider}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      disabled={!available}
                      onClick={() => set("preferred_provider", provider as ModelProvider)}
                      className={`rounded-2xl border p-3 text-left transition-all duration-300 ${
                        selected
                          ? "border-accent-edge bg-accent-wash text-accent-ink"
                          : "border-line bg-raised text-ink-muted hover:border-accent-edge"
                      } disabled:cursor-not-allowed disabled:opacity-45`}
                    >
                      <span className="flex items-center justify-between gap-2 text-[0.78rem] font-medium">
                        {label}
                        <span className="text-[0.62rem] font-normal text-ink-faint">
                          {available ? (selected ? "使用中" : "可选择") : "待配置"}
                        </span>
                      </span>
                      <span className="mt-1 block text-[0.66rem] leading-relaxed text-ink-faint">
                        {description}
                      </span>
                    </button>
                  );
                })}
              </div>
              {!profile.available_providers.doubao && (
                <p className="mt-2 text-[0.66rem] leading-relaxed text-ink-faint">
                  豆包需要在后端配置 API Key 与模型 ID 后才能启用。
                </p>
              )}
            </Section>

            <Section
              title="安全边界"
              hint="教练建议任何活动前都必须先看这两项，绝不会给出与之冲突的建议。"
            >
              <Field label="身体状况">
                <Multi
                  options={PHYSICAL_CONDITION}
                  selected={(value("physical_condition") as string[]) ?? []}
                  onToggle={(v) => toggle("physical_condition", v)}
                />
              </Field>
              <Field label="有什么是你做不了的">
                <Multi
                  options={BEHAVIOR_TABOO}
                  selected={(value("behavior_taboo") as string[]) ?? []}
                  onToggle={(v) => toggle("behavior_taboo", v)}
                />
              </Field>
            </Section>

            <Section title="沟通方式">
              <Field label="你希望我用什么方式和你说话">
                <Choices
                  options={COMMUNICATION_PREFERENCE}
                  selected={value("communication_preference") as string | null}
                  onSelect={(v) => set("communication_preference", v)}
                />
              </Field>
              <Field label="不想聊的话题" hint="选了之后我不会主动提起">
                <Multi
                  options={CONTENT_TABOO}
                  selected={(value("content_taboo") as string[]) ?? []}
                  onToggle={(v) => toggle("content_taboo", v)}
                />
              </Field>
            </Section>

            <Section
              title="身边的人"
              hint="需要有人一起的活动会优先考虑 ta 们；没有填就不会假设你有人可以一起。"
            >
              <Field label="有可以支持你的人吗">
                <Choices
                  options={["有", "暂时没有"] as const}
                  selected={
                    value("has_supporter") === true
                      ? "有"
                      : value("has_supporter") === false
                        ? "暂时没有"
                        : null
                  }
                  onSelect={(v) => set("has_supporter", v === "有")}
                />
              </Field>
              {value("has_supporter") === true && (
                <>
                  {supporters.map((entry, index) => (
                    <SupporterRow
                      key={index}
                      index={index + 1}
                      entry={entry}
                      onChange={(next) => updateSupporter(index, next)}
                      onRemove={() => removeSupporter(index)}
                    />
                  ))}
                  <button
                    type="button"
                    onClick={addSupporter}
                    className="w-full rounded-2xl border border-dashed border-line py-2.5 text-[0.78rem] text-ink-faint transition-colors duration-300 hover:border-accent-edge hover:text-accent-ink"
                  >
                    ＋ 添加一个人
                  </button>
                </>
              )}
            </Section>

            <Section title="活动偏好">
              <Field label="环境">
                <Choices
                  options={ACTIVITY_ENVIRONMENT}
                  selected={value("activity_environment") as string | null}
                  onSelect={(v) => set("activity_environment", v)}
                />
              </Field>
              <Field label="一个人还是有人陪">
                <Choices
                  options={ACTIVITY_SOCIAL}
                  selected={value("activity_social") as string | null}
                  onSelect={(v) => set("activity_social", v)}
                />
              </Field>
              <Field label="氛围">
                <Choices
                  options={ACTIVITY_INTENSITY}
                  selected={value("activity_intensity") as string | null}
                  onSelect={(v) => set("activity_intensity", v)}
                />
              </Field>
            </Section>

            <Section title="提醒">
              <Field label="多久提醒你一次">
                <Choices
                  options={REMINDER_FREQUENCY}
                  selected={value("reminder_frequency") as string | null}
                  onSelect={(v) => set("reminder_frequency", v)}
                />
              </Field>
              <Field label="什么时段">
                <Choices
                  options={REMINDER_TIME_SLOT}
                  selected={value("reminder_time_slot") as string | null}
                  onSelect={(v) => set("reminder_time_slot", v)}
                />
              </Field>
            </Section>

            {error && (
              <p role="alert" className="mb-3 rounded-xl bg-alert-wash px-3 py-2.5 text-[0.78rem] text-alert-ink">
                {error}
              </p>
            )}

            <div className="sticky bottom-0 -mx-6 -mb-6 mt-6 rounded-b-[28px] border-t border-line bg-panel px-6 py-4 backdrop-blur-2xl">
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || !dirty}
                className="w-full rounded-2xl border border-accent-edge bg-accent-wash py-2.5 text-[0.85rem] text-accent-ink transition-opacity duration-300 hover:opacity-85 disabled:opacity-40"
              >
                {saving ? "保存中…" : saved && !dirty ? "已保存" : "保存修改"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const inputClass =
  "w-full rounded-xl border border-line bg-raised px-3 py-2.5 text-[0.85rem] text-ink outline-none transition-colors duration-300 focus:border-accent-edge";

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6 border-t border-line pt-4 first:border-t-0 first:pt-0">
      <h3 className="text-[0.82rem] text-ink">{title}</h3>
      {hint && (
        <p className="mt-1 text-[0.68rem] leading-relaxed text-ink-faint">{hint}</p>
      )}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="mb-4 block last:mb-0">
      <span className="mb-1.5 block text-[0.76rem] text-ink-muted">{label}</span>
      {hint && <span className="mb-1.5 block text-[0.68rem] text-ink-faint">{hint}</span>}
      {children}
    </label>
  );
}

/** Single choice. Clicking the selected one again clears it. */
function Choices({
  options,
  selected,
  onSelect,
}: {
  options: readonly string[];
  selected: string | null;
  onSelect: (value: string | null) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((option) => (
        <Chip
          key={option}
          label={option}
          selected={selected === option}
          onClick={() => onSelect(selected === option ? null : option)}
        />
      ))}
    </div>
  );
}

function Multi({
  options,
  selected,
  onToggle,
}: {
  options: readonly string[];
  selected: string[];
  onToggle: (value: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((option) => (
        <Chip
          key={option}
          label={option}
          selected={selected.includes(option)}
          onClick={() => onToggle(option)}
        />
      ))}
    </div>
  );
}

function Chip({
  label,
  selected,
  onClick,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={`rounded-full border px-2.5 py-1 text-[0.72rem] transition-colors duration-300 ${
        selected
          ? "border-accent-edge bg-accent-wash text-accent-ink"
          : "border-line text-ink-faint hover:text-ink-muted"
      }`}
    >
      {label}
    </button>
  );
}

function SupporterRow({
  index,
  entry,
  onChange,
  onRemove,
}: {
  index: number;
  entry: Supporter;
  onChange: (next: Supporter) => void;
  onRemove: () => void;
}) {
  // The six suggestions come from the legacy ENUM, but `relation` is free
  // text here — picking a chip fills the box, and anything typed over it is
  // equally valid. See the backend's `Supporter`.
  return (
    <div className="mb-4 rounded-2xl border border-line p-3 last:mb-0">
      <div className="mb-2.5 flex items-center justify-between">
        <p className="text-[0.72rem] text-ink-faint">支持者 {index}</p>
        <button
          type="button"
          onClick={onRemove}
          aria-label={`移除支持者 ${index}`}
          className="rounded-full px-2 py-0.5 text-[0.72rem] text-ink-faint transition-colors duration-300 hover:bg-alert-wash hover:text-alert-ink"
        >
          移除
        </button>
      </div>

      <Field label="关系" hint="点一个，或者自己写">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {SUPPORTER_RELATION.map((option) => (
            <Chip
              key={option}
              label={option}
              selected={entry.relation === option}
              onClick={() =>
                onChange({ ...entry, relation: entry.relation === option ? "" : option })
              }
            />
          ))}
        </div>
        <input
          value={entry.relation}
          onChange={(e) => onChange({ ...entry, relation: e.target.value })}
          maxLength={32}
          placeholder="例如：室友、教练、同学"
          className={inputClass}
        />
      </Field>

      <Field label="怎么称呼 ta">
        <input
          value={entry.nickname ?? ""}
          onChange={(e) => onChange({ ...entry, nickname: e.target.value || null })}
          maxLength={64}
          className={inputClass}
        />
      </Field>

      <Field label="ta 对你的影响有多大">
        <Choices
          options={SUPPORTER_INFLUENCE}
          selected={entry.influence}
          onSelect={(v) => onChange({ ...entry, influence: v })}
        />
      </Field>
    </div>
  );
}
