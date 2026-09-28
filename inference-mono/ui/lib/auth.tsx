"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { toast } from "sonner";

import { PageLoadingState } from "@/components/loading-spinner";
import { apiErrorMessage, authApi, clearSsoCookie, syncSsoCookie, userApi } from "@/lib/api";
import { authStore, tokenResponseToSession } from "@/lib/auth-store";
import type { AuthSession, TokenResponse, User } from "@/lib/types";

type AuthContextValue = {
  ready: boolean;
  session: AuthSession | null;
  user: User | null;
  isAuthenticated: boolean;
  setTokenResponse: (response: TokenResponse) => void;
  setUser: (user: User) => void;
  logout: () => Promise<void>;
  refreshUser: () => Promise<User | null>;
  /** Mint/refresh the httpOnly SSO cookie and resolve once it is set. */
  ensureSsoCookie: () => Promise<void>;
};

/** The chat front door, which may be a path here or its own hostname. */
const CHAT_URL = process.env.NEXT_PUBLIC_CHAT_URL || "/chat";

/**
 * Where a post-login redirect is allowed to go. `next` arrives from the chat front
 * door bouncing an unauthenticated caller here, so it is attacker-influenced: any
 * other absolute URL, or a protocol-relative "//host", would make this an open
 * redirect. Exactly one off-origin destination is permitted — the chat URL this
 * build is configured with, path/query and all (e.g. `${CHAT_URL}?q=...&submit=false`
 * from `buildChatPromptUrl`), not just an exact string match against the bare
 * `CHAT_URL` value.
 */
export function safeNextPath(raw: string | null | undefined, fallback = "/dashboard"): string {
  if (!raw) return fallback;
  try {
    // Both sides must be absolute for `new URL` to succeed without a base. If
    // CHAT_URL is itself a same-origin relative path (the default, "/chat"),
    // this throws and we fall through to the relative-path check below, which
    // already allows it.
    const nextOrigin = new URL(raw).origin;
    const chatOrigin = new URL(CHAT_URL).origin;
    if (nextOrigin === chatOrigin) return raw;
  } catch {
    // `raw` isn't an absolute URL, or CHAT_URL isn't one to compare against.
  }
  if (!raw.startsWith("/") || raw.startsWith("//")) return fallback;
  return raw;
}

/**
 * `router.replace` cannot leave the origin, so an off-origin destination needs a
 * real browser navigation. Everything else stays a client-side transition.
 */
export function navigateAfterAuth(
  router: { replace: (path: string) => void },
  next: string
): void {
  if (/^https?:\/\//.test(next)) {
    window.location.assign(next);
    return;
  }
  router.replace(next);
}

const AuthContext = React.createContext<AuthContextValue | undefined>(undefined);
const IDENTITY_STALE_TIME_MS = 5 * 60_000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = React.useState(false);
  const [session, setSession] = React.useState<AuthSession | null>(null);
  const queryClient = useQueryClient();

  React.useEffect(() => {
    // `revalidate` controls whether the seeded value is stamped as already
    // stale (`updatedAt: 0`). We still want it to paint instantly (avoiding
    // an auth flash), but a query seeded this way is eligible for the
    // automatic background refetch React Query performs on mount/subscribe,
    // so a snapshot that has drifted from the server (or is just old) gets
    // corrected without the caller having to do anything. Writes we already
    // know are fresh -- e.g. a response we just wrote to localStorage
    // ourselves -- should NOT be re-marked stale, or every local write would
    // trigger a redundant extra fetch.
    const syncSession = (revalidate: boolean) => {
      const nextSession = authStore.read();
      if (nextSession?.user) {
        queryClient.setQueryData(
          ["me"],
          nextSession.user,
          revalidate ? { updatedAt: 0 } : undefined
        );
      } else {
        queryClient.removeQueries({ queryKey: ["me"] });
      }
      setSession(nextSession);
    };

    // Initial mount: localStorage may hold a snapshot that is minutes old,
    // or was written by another tab, so force a real revalidation once the
    // ["me"] query becomes enabled.
    syncSession(true);
    setReady(true);

    const handleExternalChange = () => syncSession(false);
    window.addEventListener("storage", handleExternalChange);
    window.addEventListener("inference-auth-changed", handleExternalChange);
    return () => {
      window.removeEventListener("storage", handleExternalChange);
      window.removeEventListener("inference-auth-changed", handleExternalChange);
    };
  }, [queryClient]);

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: userApi.me,
    enabled: ready && Boolean(session?.accessToken),
    retry: false,
    staleTime: IDENTITY_STALE_TIME_MS
  });

  React.useEffect(() => {
    if (meQuery.data) {
      authStore.updateUser(meQuery.data);
      setSession(authStore.read());
    }
  }, [meQuery.data]);

  React.useEffect(() => {
    if (meQuery.error) {
      toast.error(apiErrorMessage(meQuery.error));
    }
  }, [meQuery.error]);

  const setTokenResponse = React.useCallback(
    (response: TokenResponse) => {
      const nextSession = tokenResponseToSession(response);
      authStore.write(nextSession);
      setSession(nextSession);
      queryClient.setQueryData(["me"], response.user);
      // Best-effort: mint the httpOnly SSO cookie so /chat SSO works.
      void syncSsoCookie();
    },
    [queryClient]
  );

  const logout = React.useCallback(async () => {
    const refreshToken = authStore.refreshToken();
    try {
      await authApi.logout(refreshToken);
    } catch {
      // Local cleanup still needs to happen if the server-side token is already invalid.
    } finally {
      authStore.clear();
      void clearSsoCookie();
      setSession(null);
      queryClient.clear();
    }
  }, [queryClient]);

  const setUser = React.useCallback(
    (user: User) => {
      // Seed the cache and localStorage straight from data we already know
      // is authoritative (e.g. a mutation's own response), rather than
      // trusting locally-optimistic state or triggering another round-trip.
      // `dataUpdatedAt` defaults to "now" here, which is correct: this value
      // really was just fetched.
      queryClient.setQueryData(["me"], user);
      authStore.updateUser(user);
      setSession(authStore.read());
    },
    [queryClient]
  );

  const refreshUser = React.useCallback(async () => {
    try {
      // `staleTime: 0` is required here: fetchQuery honors staleTime and
      // will silently hand back a cached (possibly stale) user instead of
      // hitting the network if the cache entry is still within its
      // freshness window. A deliberate refresh must always talk to the
      // server -- use refetchQueries/invalidateQueries semantics, not a
      // conditional cache read.
      const user = await queryClient.fetchQuery({
        queryKey: ["me"],
        queryFn: userApi.me,
        staleTime: 0
      });
      authStore.updateUser(user);
      setSession(authStore.read());
      return user;
    } catch (error) {
      toast.error(apiErrorMessage(error));
      return null;
    }
  }, [queryClient]);

  const ensureSsoCookie = React.useCallback(() => syncSsoCookie(), []);

  // The SSO cookie is minted at login, but a session outlives it: the cookie has
  // its own (shorter) TTL, and a deployment change — a new cookie domain, a
  // rotated SSO_SECRET — invalidates whatever the browser already holds. Without
  // this, an already-signed-in user can never reach chat again: the chat front
  // door sees no valid cookie and bounces them back here.
  const syncedTokenRef = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!ready) return;
    const token = session?.accessToken ?? null;
    if (!token) {
      syncedTokenRef.current = null;
      return;
    }
    if (syncedTokenRef.current === token) return;
    syncedTokenRef.current = token;
    void syncSsoCookie();
  }, [ready, session?.accessToken]);

  const value = React.useMemo<AuthContextValue>(
    () => ({
      ready,
      session,
      user: session?.user ?? null,
      isAuthenticated: Boolean(session?.accessToken),
      setTokenResponse,
      setUser,
      logout,
      refreshUser,
      ensureSsoCookie
    }),
    [ready, session, setTokenResponse, setUser, logout, refreshUser, ensureSsoCookie]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = React.useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

export function AuthGate({
  children,
  requireAuth = false,
  guestOnly = false
}: {
  children: React.ReactNode;
  requireAuth?: boolean;
  guestOnly?: boolean;
}) {
  const { ready, isAuthenticated } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  React.useEffect(() => {
    if (!ready) return;
    if (requireAuth && !isAuthenticated) {
      const search = window.location.search;
      const next = encodeURIComponent(
        `${pathname}${search}`
      );
      router.replace(`/login?next=${next}`);
    }
    if (guestOnly && isAuthenticated) {
      // Honour where the caller was headed. Dropping `next` here is what made the
      // chat front door bounce an already-signed-in user to /dashboard forever.
      const next = safeNextPath(new URLSearchParams(window.location.search).get("next"));
      let cancelled = false;
      void (async () => {
        // The destination may leave this origin (chat), so make sure the SSO cookie
        // exists before handing over.
        await syncSsoCookie();
        if (!cancelled) navigateAfterAuth(router, next);
      })();
      return () => {
        cancelled = true;
      };
    }
  }, [guestOnly, isAuthenticated, pathname, ready, requireAuth, router]);

  if (!ready) {
    return <PageLoadingState />;
  }

  if (requireAuth && !isAuthenticated) return null;
  if (guestOnly && isAuthenticated) return null;
  return <>{children}</>;
}
