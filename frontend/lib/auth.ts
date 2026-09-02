/**
 * Account session: the bearer token, and the calls that mint or end one.
 *
 * This replaces the localStorage UUID that `lib/identity.ts` used to hand out.
 * That value was an *assertion* — anyone could present any id — so it could
 * never gate anything. A token is a credential: the server resolves it to an
 * account and hands back `user_profile.uuid`, which is the id every clinical
 * table keys on.
 *
 * The token lives in localStorage rather than a cookie because the API is
 * called cross-origin in development (Next proxies /api, but the token still
 * has to be attached by JS) and because there is no server-rendered page that
 * needs it. That does mean an XSS bug could read it — the mitigation is that
 * the app renders no untrusted HTML, not that the storage is clever.
 */

const TOKEN_KEY = "psy-auth-token";

export interface Account {
  username: string;
  nickname: string | null;
  tag: string | null;
  display_id: string | null;
  profile_uuid: string;
  current_module: string | null;
  role: "user" | "admin";
  email: string | null;
  email_verified: boolean;
  email_required: boolean;
  email_delivery_available: boolean;
}

interface AuthPayload {
  token: string;
  expires_at: string;
  account: Account;
}

export interface RegisterInput {
  username: string;
  password: string;
  email: string;
  nickname: string;
  tag: string;
  /** Stable enough to ask once — everything else is edited in the profile. */
  age?: number | null;
  living_status?: string | null;
  communication_preference?: string | null;
  physical_condition?: string[];
  behavior_taboo?: string[];
}

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/** Read the stored token, or null. Safe when storage is blocked. */
export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Private browsing with storage blocked: the session lasts as long as the
    // page does. Signing in still works; it just won't survive a reload.
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* nothing to clear */
  }
}

function headers(json = false): HeadersInit {
  const token = getToken();
  return {
    ...(json ? { "Content-Type": "application/json" } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    // See lib/http.ts — ngrok's interstitial otherwise replaces the response.
    "ngrok-skip-browser-warning": "true",
  };
}

/** Surfaces the server's own message; the auth forms show it verbatim. */
export class AuthError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AuthError";
    this.status = status;
  }
}

async function parseAuth(res: Response): Promise<AuthPayload> {
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    // FastAPI puts the human-readable reason in `detail`; a 422 puts a list of
    // field errors there instead, which is not worth rendering raw.
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new AuthError(detail ?? "请求失败，请稍后再试", res.status);
  }
  setToken(body.token);
  return body;
}

async function parseResponse<T>(res: Response, fallback: string): Promise<T> {
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new AuthError(detail ?? fallback, res.status);
  }
  return body as T;
}

export async function register(input: RegisterInput): Promise<Account> {
  const res = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(input),
  });
  return (await parseAuth(res)).account;
}

export async function login(
  username: string,
  password: string,
): Promise<Account> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ username, password }),
  });
  return (await parseAuth(res)).account;
}

/**
 * Who the stored token belongs to, or null if there isn't one / it expired.
 * Never throws for an auth failure — a dead token is a normal signed-out
 * state, not an error the UI should report.
 */
export async function fetchMe(): Promise<Account | null> {
  if (!getToken()) return null;
  try {
    const res = await fetch(`${API_BASE}/api/auth/me`, { headers: headers() });
    if (res.status === 401) {
      clearToken();
      return null;
    }
    if (!res.ok) return null;
    return (await res.json()) as Account;
  } catch {
    // Backend unreachable. Keep the token — this is probably a dead dev
    // server, and discarding it would sign the person out for a restart.
    return null;
  }
}

/**
 * Change the signed-in account's own password.
 *
 * The current password is required by the server even though a valid token is
 * already attached — see the backend's `ChangePasswordRequest`. Other devices
 * are signed out; this one stays signed in, so the stored token is still good
 * afterwards and nothing here needs to touch it.
 */
export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/auth/password`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : null;
    throw new AuthError(detail ?? "修改失败，请稍后再试", res.status);
  }
}

export async function saveRecoveryEmail(email: string): Promise<Account> {
  const res = await fetch(`${API_BASE}/api/auth/email`, {
    method: "PUT",
    headers: headers(true),
    body: JSON.stringify({ email }),
  });
  return parseResponse<Account>(res, "邮箱保存失败，请稍后再试");
}

export async function resendEmailVerification(): Promise<string> {
  const res = await fetch(`${API_BASE}/api/auth/email/verification/resend`, {
    method: "POST",
    headers: headers(),
  });
  const body = await parseResponse<{ message: string }>(
    res,
    "验证邮件发送失败，请稍后再试",
  );
  return body.message;
}

export async function verifyEmail(token: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/auth/email/verify`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ token }),
  });
  const body = await parseResponse<{ message: string }>(
    res,
    "邮箱验证失败，请重新发送",
  );
  return body.message;
}

export async function requestPasswordReset(email: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/auth/password/forgot`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ email }),
  });
  const body = await parseResponse<{ message: string }>(
    res,
    "请求失败，请稍后再试",
  );
  return body.message;
}

export async function resetPassword(
  token: string,
  newPassword: string,
): Promise<string> {
  const res = await fetch(`${API_BASE}/api/auth/password/reset`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  const body = await parseResponse<{ message: string }>(
    res,
    "密码重置失败，请重新申请链接",
  );
  return body.message;
}

export async function logout(): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/auth/logout`, {
      method: "POST",
      headers: headers(),
    });
  } finally {
    // Local state is cleared even if the revoke call failed, so the button
    // always signs you out of this browser.
    clearToken();
  }
}
