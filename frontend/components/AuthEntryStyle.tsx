"use client";

import { useLayoutEffect, useState, type ReactNode } from "react";
import { bindOverallStyleAccount } from "@/lib/overallStyle";

/** Public reset/verification routes live outside AuthGate. */
export default function AuthEntryStyle({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  useLayoutEffect(() => {
    const unbind = bindOverallStyleAccount(null);
    setReady(true);
    return unbind;
  }, []);
  // Match SSR and hydration, and apply the root palette before showing forms.
  return ready ? children : null;
}
