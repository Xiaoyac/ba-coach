"use client";

import { useCallback, useEffect, useState } from "react";
import AuthScreen from "@/components/AuthScreen";
import ConversationWorkspace from "@/components/ConversationWorkspace";
import RequiredEmailModal from "@/components/RequiredEmailModal";
import { clearToken, fetchMe, getToken, logout, type Account } from "@/lib/auth";

interface AuthState {
  account: Account | null;
  /** Undecided on first paint — see the `checking` branch below. */
  checking: boolean;
}

/**
 * Decides between the sign-in screen and the app.
 *
 * This renders the workspace itself rather than taking it as a render prop:
 * `app/page.tsx` is a Server Component, and a function cannot be serialised
 * across that boundary ("Functions are not valid as a child of Client
 * Component"). Keeping the whole authenticated/unauthenticated switch on one
 * side of the boundary avoids the problem instead of working around it.
 *
 * The stored token is verified against the server on mount rather than
 * trusted: it can be expired or revoked (logged out from another device), and
 * the only way to know is to ask. Until that answer arrives nothing is
 * rendered — flashing the sign-in form at someone who *is* signed in is worse
 * than a beat of blank space, and rendering the app first would fire a burst
 * of requests that all 401.
 */
export default function AuthGate() {
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    const expire = () => {
      clearToken();
      setExpired(true);
      setState({ account: null, checking: false });
    };
    window.addEventListener("bacoach-auth-expired", expire);
    return () => window.removeEventListener("bacoach-auth-expired", expire);
  }, []);
  // Always `checking` on the first render, on both sides. Deriving it from
  // `getToken()` instead would read localStorage — which does not exist during
  // SSR — so the server would render the sign-in screen while the client
  // rendered the spinner, and React would report a hydration mismatch and
  // throw away the server HTML. The token is inspected in the effect below,
  // which only ever runs on the client.
  const [state, setState] = useState<AuthState>({
    account: null,
    checking: true,
  });

  useEffect(() => {
    let cancelled = false;

    // No token: signed out, and no round trip needed to know it.
    if (!getToken()) {
      setState({ account: null, checking: false });
      return;
    }

    fetchMe().then((account) => {
      if (!cancelled) setState({ account, checking: false });
    });
    return () => {
      cancelled = true;
    };
    // Deliberately mount-only: this is the initial "am I signed in?" probe.
    // Re-running it on every state change would re-check after every sign-in.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLogout = useCallback(async () => {
    await logout();
    setState({ account: null, checking: false });
  }, []);

  const handleAccountRefresh = useCallback(async () => {
    const account = await fetchMe();
    if (account) setState({ account, checking: false });
  }, []);

  if (state.checking) {
    return (
      <div
        className="flex w-full items-center justify-center"
        // Silent for assistive tech: this is a sub-second gap, and announcing
        // "loading" for it is noise.
        aria-hidden
      >
        <div className="h-6 w-6 animate-pulse rounded-full bg-raised" />
      </div>
    );
  }

  if (!state.account) {
    return (
      <>
        {expired && <p role="alert" className="fixed top-3 left-4 right-4 z-50 rounded-xl bg-panel p-3 text-center text-sm text-alert-ink">登录已失效，请重新登录。已保存的记录不会丢失。</p>}
        <AuthScreen onAuthenticated={(account) => { setExpired(false); setState({ account, checking: false }); }} />
      </>
    );
  }

  return (
    <>
      <ConversationWorkspace
        displayName={state.account.nickname || state.account.username}
        accountUsername={state.account.username}
        displayIdentity={state.account.display_id}
        accountRole={state.account.role}
        accountEmail={state.account.email}
        accountEmailVerified={state.account.email_verified}
        emailDeliveryAvailable={state.account.email_delivery_available}
        onAccountRefresh={handleAccountRefresh}
        onLogout={handleLogout}
      />
      {state.account.email_required && (
        <RequiredEmailModal
          deliveryAvailable={state.account.email_delivery_available}
          onSaved={(account) => setState({ account, checking: false })}
        />
      )}
    </>
  );
}
