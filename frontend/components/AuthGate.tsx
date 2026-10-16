"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { bindOverallStyleAccount } from "@/lib/overallStyle";
import { usePathname } from "next/navigation";
import SettingsPage from "@/components/SettingsPage";
import AuthScreen from "@/components/AuthScreen";
import ThemeToggle from "@/components/ThemeToggle";
import ConversationWorkspace from "@/components/ConversationWorkspace";
import RequiredEmailModal from "@/components/RequiredEmailModal";
import RequiredBirthYearModal from "@/components/RequiredBirthYearModal";
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
  const inSettings = usePathname() === "/settings";
  const [settingsVisited, setSettingsVisited] = useState(inSettings);
  useEffect(() => {
    if (inSettings) setSettingsVisited(true);
    else {
      const frame = requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
      return () => cancelAnimationFrame(frame);
    }
  }, [inSettings]);
  const authGeneration = useRef(0);
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    const expire = () => {
      authGeneration.current++;
      void import('@/lib/push').then(m=>m.clearLocalPush()).catch(()=>{});
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
    const verify = () => {
      const generation = ++authGeneration.current;
      const token = getToken();
      // Hide the old workspace before resolving a changed account or a
      // back/forward cache restore. Late old-account reads cannot repaint it.
      setState({ account: null, checking: Boolean(token) });
      if (!token) return;
      void fetchMe().then((account) => {
        if (generation === authGeneration.current && (token === getToken() || (!account && !getToken()))) {
          setState({ account, checking: false });
        }
      });
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === "psy-auth-token" || event.key === null) verify();
    };
    const onPageShow = (event: PageTransitionEvent) => { if (event.persisted) verify(); };
    verify();
    window.addEventListener("storage", onStorage);
    window.addEventListener("pageshow", onPageShow);
    return () => {
      // This is a request epoch, not a DOM ref; invalidate its latest value.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      authGeneration.current++;
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("pageshow", onPageShow);
    };
    // Install once; token changes and BFCache restores explicitly revalidate.
  }, []);

  const handleLogout = useCallback(async () => {
    const generation = ++authGeneration.current;
    await logout();
    if (generation === authGeneration.current) setState({ account: null, checking: false });
  }, []);

  const handleAccountRefresh = useCallback(async () => {
    const generation = authGeneration.current;
    const token = getToken();
    const account = await fetchMe();
    if (account && generation === authGeneration.current && token === getToken()) setState({ account, checking: false });
  }, []);

  const styleAccountId = state.checking ? undefined : state.account?.profile_uuid;
  const styleAccountRole = state.checking ? undefined : state.account?.role;
  useLayoutEffect(() => bindOverallStyleAccount(styleAccountId && styleAccountRole
    ? { profile_uuid: styleAccountId, role: styleAccountRole } : null), [styleAccountId, styleAccountRole]);

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
        <AuthScreen onAuthenticated={(account) => { authGeneration.current++; setExpired(false); setState({ account, checking: false }); }} />
        <ThemeToggle />
      </>
    );
  }

  if (state.account.birth_year_required) {
    return <RequiredBirthYearModal onSaved={(account) => setState({ account, checking: false })} onLogout={handleLogout} />;
  }

  return (
    <>
      <div data-chat-workspace inert={inSettings} className="flex h-full min-h-0 w-full"><ConversationWorkspace
        key={`${state.account.profile_uuid}:${state.account.role}`}
        accountId={state.account.profile_uuid}
        displayName={state.account.nickname || state.account.username}
        accountUsername={state.account.username}
        accountRole={state.account.role}
        accountEmail={state.account.email}
        accountEmailVerified={state.account.email_verified}
        emailDeliveryAvailable={state.account.email_delivery_available}
        onAccountRefresh={handleAccountRefresh}
        onLogout={handleLogout}
      /></div>
      {settingsVisited && <SettingsPage
        key={`${state.account.profile_uuid}:${state.account.role}`} active={inSettings && !state.account.email_required} account={state.account}
        onAccountRefresh={handleAccountRefresh} onLogout={handleLogout}
      />}
      {state.account.email_required && (
        <RequiredEmailModal
          deliveryAvailable={state.account.email_delivery_available}
          onSaved={(account) => setState({ account, checking: false })}
        />
      )}
    </>
  );
}
