// An admin's "体验被试界面" switch, saved per account in this browser. It only
// changes which UI is rendered; the server still sees the admin account.
const memory = new Map<string, boolean>();

function key(accountId: string): string {
  return `bacoach-member-preview:${accountId}`;
}

export function readMemberPreview(accountId: string): boolean {
  if (!accountId) return false;
  try { return localStorage.getItem(key(accountId)) === "1"; }
  catch { return memory.get(accountId) ?? false; }
}

export function saveMemberPreview(accountId: string, on: boolean): void {
  if (!accountId) return;
  memory.set(accountId, on);
  try {
    if (on) localStorage.setItem(key(accountId), "1");
    else localStorage.removeItem(key(accountId));
  } catch { /* Still switches for this page without browser storage. */ }
}
