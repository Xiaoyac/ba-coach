"use client";

import { useSyncExternalStore } from "react";
import { chooseOverallStyle, getOverallStyle, subscribeOverallStyle } from "@/lib/overallStyle";

const options = [
  { id: "default", name: "默认", description: "保留原有界面样式。" },
  { id: "simple", name: "简约", description: "用留白和浅底色区分内容，减少边框与装饰。" },
  { id: "participant-1", name: "被试建议1", description: "精简说明、分组设置、抽屉目标卡与清晰表格。" },
] as const;

export default function OverallStyleSelector() {
  const style = useSyncExternalStore(subscribeOverallStyle, getOverallStyle, () => "default" as const);
  return <section aria-labelledby="overall-style-heading" className="overall-style-section">
    <h3 id="overall-style-heading" className="text-sm font-medium">整体风格<span className="ml-2 text-xs font-normal text-ink-faint">管理员专享</span></h3>
    <fieldset className="overall-style-options">
      <legend className="sr-only">整体风格</legend>
      {options.map(option => <label key={option.id} data-selected={style === option.id} className={`overall-style-option cursor-pointer rounded-xl transition-colors ${style === option.id ? "bg-accent-wash text-accent-ink" : "bg-raised/50 text-ink-muted hover:bg-raised"}`}>
        <span><span className="block text-sm font-medium">{option.name}</span><span className="mt-1 block text-xs leading-5 text-ink-muted">{option.description}</span></span>
        <input type="radio" name="overall-style" value={option.id} checked={style === option.id}
          onChange={() => chooseOverallStyle(option.id)} className="h-4 w-4 shrink-0 accent-accent" />
      </label>)}
    </fieldset>
    <p className="mt-3 text-xs leading-6 text-ink-faint">仅影响你在当前浏览器中的界面，选择会自动保存。</p>
  </section>;
}
