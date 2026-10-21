/** Admin-only pacing for the fast opening, saved per admin in this browser. */
export const LEAD_INTERVAL_MIN_MS = 0;
export const LEAD_INTERVAL_MAX_MS = 1000;
export const LEAD_INTERVAL_KEY_PREFIX = "bacoach-lead-interval:";

type PacingAccount = { profile_uuid: string; role: string } | null;
let account: PacingAccount = null;
// null = the default adaptive pacing; a number = a fixed interval in ms.
let active: number | null = null;
const listeners = new Set<() => void>();
const memory = new Map<string, number | null>();

export function getLeadInterval(): number | null { return active; }
export function subscribeLeadInterval(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function valid(value: unknown): number | null {
  const ms = typeof value === "string" ? Number(value) : value;
  return typeof ms === "number" && Number.isFinite(ms) && ms >= LEAD_INTERVAL_MIN_MS && ms <= LEAD_INTERVAL_MAX_MS
    ? Math.round(ms) : null;
}

function apply(value: number | null) {
  active = value;
  listeners.forEach(listener => listener());
}

function restore() {
  if (account?.role !== "admin") { apply(null); return; }
  const key = LEAD_INTERVAL_KEY_PREFIX + account.profile_uuid;
  let value: unknown;
  try { value = localStorage.getItem(key); }
  catch { value = memory.get(key); }
  apply(valid(value));
}

/** Called only with the identity verified by AuthGate, never a cached role. */
export function bindLeadPacingAccount(next: PacingAccount) {
  account = next;
  restore();
  const stored = (event: StorageEvent) => {
    if (event.key === null || event.key === LEAD_INTERVAL_KEY_PREFIX + account?.profile_uuid) restore();
  };
  window.addEventListener("storage", stored);
  return () => {
    window.removeEventListener("storage", stored);
    account = null;
    apply(null);
  };
}

/** A fixed interval in ms, or null to return to the default adaptive pacing. */
export function chooseLeadInterval(value: number | null) {
  if (account?.role !== "admin") { restore(); return; }
  const safe = valid(value);
  const key = LEAD_INTERVAL_KEY_PREFIX + account.profile_uuid;
  memory.set(key, safe);
  try {
    if (safe === null) localStorage.removeItem(key);
    else localStorage.setItem(key, String(safe));
  } catch { /* Still usable without browser storage. */ }
  apply(safe);
}
