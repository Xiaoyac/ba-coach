"use client";

import { useSyncExternalStore } from "react";
import { SettingHelp } from "@/components/ConversationControls";
import {
  LEAD_INTERVAL_MAX_MS, LEAD_INTERVAL_MIN_MS, chooseLeadEnabled, chooseLeadInterval, getLeadEnabled, getLeadInterval,
  subscribeLeadInterval,
} from "@/lib/leadPacing";

const DEFAULT_START_MS = 280;

export default function LeadSpeedSetting() {
  const enabled = useSyncExternalStore(subscribeLeadInterval, getLeadEnabled, () => false);
  const interval = useSyncExternalStore(subscribeLeadInterval, getLeadInterval, () => null);
  const isDefault = interval === null;
  const value = interval ?? DEFAULT_START_MS;
  return <section aria-labelledby="lead-speed-heading" className="overall-style-section">
    <h3 id="lead-speed-heading" className="text-sm font-medium">快速接话（承接开头）<span className="ml-2 text-xs font-normal text-ink-faint">管理员专享</span></h3>
    <div className="mt-3 flex items-center gap-2 rounded-xl bg-raised/50 px-4 py-3">
      <label htmlFor="lead-enabled-switch" className="text-sm text-ink">在我的对话中显示承接开头</label>
      <SettingHelp hover align="start" id="lead-enabled-details" label="承接开头说明">
        <p>打开后，你的「自然接话＋深度回复」对话会先逐字显示一句简短接话，再接着显示主回复。关闭时只显示主回复。</p>
        <p className="mt-2">只影响你自己的对话，不影响其他管理员和用户。「现有回复」模式的对话没有承接开头。</p>
      </SettingHelp>
      <button id="lead-enabled-switch" type="button" role="switch" aria-checked={enabled} onClick={() => chooseLeadEnabled(!enabled)}
        className={`ml-auto inline-flex h-6 w-11 shrink-0 items-center rounded-full p-0.5 transition-colors ${enabled ? "bg-accent" : "bg-line-strong"}`}>
        <span aria-hidden="true" className={`size-5 rounded-full bg-sheet shadow transition-transform ${enabled ? "translate-x-5" : ""}`} />
      </button>
    </div>
    {enabled && <div className="mt-3 rounded-xl bg-raised/50 px-4 py-3">
      <div className="flex items-center gap-2">
        <label htmlFor="lead-speed-slider" className="text-sm text-ink">每字间隔</label>
        <SettingHelp hover align="start" id="lead-speed-slider-details" label="每字间隔说明">
          <p>拖动设置快速接话开头每个字出现的间隔：0ms 即时显示（不再逐字），1000ms 最慢（每秒 1 字）。</p>
          <p className="mt-2">设置后按固定间隔逐字显示。正文一开始输出，开头剩余的字会立即补完。</p>
        </SettingHelp>
        <span className="ml-auto text-sm font-medium tabular-nums text-ink" aria-live="polite">
          {isDefault ? "默认（自适应）" : value === 0 ? "0 ms · 即时" : `${value} ms`}
        </span>
      </div>
      <input id="lead-speed-slider" type="range" min={LEAD_INTERVAL_MIN_MS} max={LEAD_INTERVAL_MAX_MS} step={10}
        value={value} aria-valuetext={isDefault ? "默认自适应速度" : `${value} 毫秒`}
        onChange={event => chooseLeadInterval(Number(event.target.value))}
        className={`mt-3 w-full cursor-pointer accent-accent ${isDefault ? "opacity-60" : ""}`} />
      <div aria-hidden="true" className="mt-1 flex justify-between text-xs tabular-nums text-ink-faint">
        <span>{LEAD_INTERVAL_MIN_MS} ms · 快</span><span>慢 · {LEAD_INTERVAL_MAX_MS} ms</span>
      </div>
      <div className="mt-3 flex items-center gap-1">
        <button type="button" onClick={() => chooseLeadInterval(null)} disabled={isDefault}
          className="surface-button rounded-lg border border-line px-3 py-1.5 text-xs text-ink hover:bg-raised disabled:cursor-default disabled:opacity-50">
          恢复默认
        </button>
        <SettingHelp hover align="start" id="lead-speed-default-details" label="默认速度说明">
          <p>默认是自适应速度：每字间隔 = 280ms + 已显示时间 × 0.035，最长 500ms。</p>
          <p className="mt-2">也就是开始约每秒 3.5 字，显示约 6 秒后放慢到每秒 2 字。</p>
        </SettingHelp>
      </div>
    </div>}
    <p className="mt-3 text-xs leading-6 text-ink-faint">选择保存在当前浏览器，只对你的账号生效。</p>
  </section>;
}
