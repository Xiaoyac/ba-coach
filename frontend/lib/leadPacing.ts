/** Admin-only switch and pacing for the fast opening, saved per admin in this browser. */
export const LEAD_INTERVAL_MIN_MS = 0;
export const LEAD_INTERVAL_MAX_MS = 1000;
export const LEAD_INTERVAL_KEY_PREFIX = "bacoach-lead-interval:";
export const LEAD_ENABLED_KEY_PREFIX = "bacoach-lead-enabled:";

type PacingAccount = { profile_uuid: string; role: string } | null;
let account: PacingAccount = null;
// null = the default adaptive pacing; a number = a fixed interval in ms.
let active: number | null = null;
// Off unless this admin turned the opening on for their own chats.
let enabled = false;
const listeners = new Set<() => void>();
const memory = new Map<string, number | null>();
const enabledMemory = new Map<string, boolean>();

export function getLeadInterval(): number | null { return active; }
export function getLeadEnabled(): boolean { return enabled; }
/** Sent with each chat request; undefined for members, whose replies follow the server default. */
export function leadRequestFlag(): boolean | undefined {
  return account?.role === "admin" ? enabled : undefined;
}
export function subscribeLeadInterval(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function valid(value: unknown): number | null {
  const ms = typeof value === "string" ? Number(value) : value;
  return typeof ms === "number" && Number.isFinite(ms) && ms >= LEAD_INTERVAL_MIN_MS && ms <= LEAD_INTERVAL_MAX_MS
    ? Math.round(ms) : null;
}

function apply(value: number | null, on = enabled) {
  active = value;
  enabled = on;
  listeners.forEach(listener => listener());
}

function restore() {
  if (account?.role !== "admin") { apply(null, false); return; }
  const key = LEAD_INTERVAL_KEY_PREFIX + account.profile_uuid;
  const onKey = LEAD_ENABLED_KEY_PREFIX + account.profile_uuid;
  let value: unknown;
  let on: boolean;
  try { value = localStorage.getItem(key); on = localStorage.getItem(onKey) === "1"; }
  catch { value = memory.get(key); on = enabledMemory.get(onKey) ?? false; }
  apply(valid(value), on);
}

/** Called only with the identity verified by AuthGate, never a cached role. */
export function bindLeadPacingAccount(next: PacingAccount) {
  account = next;
  restore();
  const stored = (event: StorageEvent) => {
    if (event.key === null || event.key === LEAD_INTERVAL_KEY_PREFIX + account?.profile_uuid
      || event.key === LEAD_ENABLED_KEY_PREFIX + account?.profile_uuid) restore();
  };
  window.addEventListener("storage", stored);
  return () => {
    window.removeEventListener("storage", stored);
    account = null;
    apply(null, false);
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

/** Turn the fast opening on or off for this admin's own chats. */
export function chooseLeadEnabled(on: boolean) {
  if (account?.role !== "admin") { restore(); return; }
  const key = LEAD_ENABLED_KEY_PREFIX + account.profile_uuid;
  enabledMemory.set(key, on);
  try {
    if (on) localStorage.setItem(key, "1");
    else localStorage.removeItem(key);
  } catch { /* Still usable without browser storage. */ }
  apply(active, on);
}
