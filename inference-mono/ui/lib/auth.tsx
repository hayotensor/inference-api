"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";
import { toast } from "sonner";

import { PageLoadingState } from "@/components/loading-spinner";
import { apiErrorMessage, authApi, userApi } from "@/lib/api";
import { authStore, tokenResponseToSession } from "@/lib/auth-store";
import type { AuthSession, TokenResponse, User } from "@/lib/types";

type AuthContextValue = {
  ready: boolean;
  session: AuthSession | null;
  user: User | null;
  isAuthenticated: boolean;
  setTokenResponse: (response: TokenResponse) => void;
  logout: () => Promise<void>;
  refreshUser: () => Promise<User | null>;
};

const AuthContext = React.createContext<AuthContextValue | undefined>(undefined);
const IDENTITY_STALE_TIME_MS = 5 * 60_000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = React.useState(false);
  const [session, setSession] = React.useState<AuthSession | null>(null);
  const queryClient = useQueryClient();

  React.useEffect(() => {
    const syncSession = () => {
      const nextSession = authStore.read();
      if (nextSession?.user) {
        queryClient.setQueryData(["me"], nextSession.user);
      } else {
        queryClient.removeQueries({ queryKey: ["me"] });
      }
      setSession(nextSession);
    };

    syncSession();
    setReady(true);

    window.addEventListener("storage", syncSession);
    window.addEventListener("inference-auth-changed", syncSession);
    return () => {
      window.removeEventListener("storage", syncSession);
      window.removeEventListener("inference-auth-changed", syncSession);
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
      setSession(null);
      queryClient.clear();
    }
  }, [queryClient]);

  const refreshUser = React.useCallback(async () => {
    try {
      const user = await queryClient.fetchQuery({
        queryKey: ["me"],
        queryFn: userApi.me
      });
      authStore.updateUser(user);
      setSession(authStore.read());
      return user;
    } catch (error) {
      toast.error(apiErrorMessage(error));
      return null;
    }
  }, [queryClient]);

  const value = React.useMemo<AuthContextValue>(
    () => ({
      ready,
      session,
      user: session?.user ?? null,
      isAuthenticated: Boolean(session?.accessToken),
      setTokenResponse,
      logout,
      refreshUser
    }),
    [ready, session, setTokenResponse, logout, refreshUser]
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
      router.replace("/dashboard");
    }
  }, [guestOnly, isAuthenticated, pathname, ready, requireAuth, router]);

  if (!ready) {
    return <PageLoadingState />;
  }

  if (requireAuth && !isAuthenticated) return null;
  if (guestOnly && isAuthenticated) return null;
  return <>{children}</>;
}
