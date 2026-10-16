"use client";

import { useSyncExternalStore } from "react";
import { getOverallStyle, subscribeOverallStyle } from "@/lib/overallStyle";

export function useParticipantStyle() {
  return useSyncExternalStore(subscribeOverallStyle, getOverallStyle, () => "default") === "participant-1";
}

export function useClaudeStyle() {
  return useSyncExternalStore(subscribeOverallStyle, getOverallStyle, () => "default") === "claude";
}
