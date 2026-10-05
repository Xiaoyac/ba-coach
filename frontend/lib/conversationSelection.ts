// Only an ID, never messages or cards. Tab-local and scoped to the verified
// profile UUID; it is a navigation preference, not proof of access.
function key(accountId: string): string {
  return `bacoach:selected-conversation:${accountId}`;
}

export function readConversationSelection(accountId: string): string | null {
  if (!accountId) return null;
  try {
    const id = sessionStorage.getItem(key(accountId));
    return id && id.length <= 256 ? id : null;
  } catch {
    return null;
  }
}

export function rememberConversationSelection(accountId: string, sessionId: string): void {
  if (!accountId) return;
  try { sessionStorage.setItem(key(accountId), sessionId); } catch { /* Storage may be blocked. */ }
}

export function forgetConversationSelection(accountId: string, sessionId: string): void {
  if (readConversationSelection(accountId) !== sessionId) return;
  try { sessionStorage.removeItem(key(accountId)); } catch { /* Navigation still works. */ }
}
