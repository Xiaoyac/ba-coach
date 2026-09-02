/**
 * Browser-local facts about the caller that are not identity.
 *
 * This module used to mint and store a `subject_id` UUID. That is gone:
 * identity now comes from an authenticated session (see `lib/auth.ts`), and
 * the server derives `subject_id` from the token as `user_profile.uuid`.
 * Only the timezone is still genuinely a property of *this browser* rather
 * than of the account.
 */

/** The browser's IANA zone, e.g. "Asia/Shanghai". Used to resolve "today". */
export function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
