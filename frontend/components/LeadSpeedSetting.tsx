"use client";

import { useSyncExternalStore } from "react";
import { SettingHelp } from "@/components/ConversationControls";
import {
  LEAD_INTERVAL_MAX_MS, LEAD_INTERVAL_MIN_MS, chooseLeadInterval, getLeadInterval, subscribeLeadInterval,
} from "@/lib/leadPacing";

const DEFAULT_START_MS = 280;

export default function LeadSpeedSetting() {
  const interval = useSyncExternalStore(subscribeLeadInterval, getLeadInterval, () => null);
  const isDefault = interval === null;
  const value = interval ?? DEFAULT_START_MS;
  return <section aria-labelledby="lead-speed-heading" className="overall-style-section">
    <h3 id="lead-speed-heading" className="text-sm font-medium">快速接话显示速度<span className="ml-2 text-xs font-normal text-ink-faint">管理员专享</span></h3>
    <div className="mt-3 rounded-xl bg-raised/50 px-4 py-3">
      <div className="flex items-center gap-2">
        <label htmlFor="lead-speed-slider" className="text-sm text-ink">每字间隔</label>
        <SettingHelp hover align="start" id="lead-speed-slider-details" label="每字间隔说明">
          <p>拖动设置快速接话开头每个字出现的间隔：100ms 最快（约每秒 10 字），1000ms 最慢（每秒 1 字）。</p>
          <p className="mt-2">设置后按固定间隔逐字显示。正文一开始输出，开头剩余的字会立即补完。</p>
        </SettingHelp>
        <span className="ml-auto text-sm font-medium tabular-nums text-ink" aria-live="polite">
          {isDefault ? "默认（自适应）" : `${value} ms`}
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
    </div>
    <p className="mt-3 text-xs leading-6 text-ink-faint">仅影响你在当前浏览器中看到的快速接话，选择会自动保存。</p>
  </section>;
}
