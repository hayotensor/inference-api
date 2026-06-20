"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";

import { AuthFormShell } from "@/components/auth-form-shell";
import { Button } from "@/components/ui/button";
import { authApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { TokenResponse } from "@/lib/types";

export default function OAuthCallbackPage() {
  const params = useParams<{ provider: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setTokenResponse } = useAuth();
  const [error, setError] = useState<string | null>(null);

  const provider = params.provider === "apple" ? "apple" : "google";
  const search = useMemo(() => {
    const text = searchParams.toString();
    return text ? `?${text}` : "";
  }, [searchParams]);

  useEffect(() => {
    let cancelled = false;

    async function completeOAuth() {
      try {
        const accessToken = searchParams.get("access_token");
        const refreshToken = searchParams.get("refresh_token");
        const userParam = searchParams.get("user");
        if (accessToken && refreshToken && userParam) {
          const parsed = JSON.parse(decodeURIComponent(userParam));
          setTokenResponse({
            access_token: accessToken,
            refresh_token: refreshToken,
            token_type: "bearer",
            expires_in: Number(searchParams.get("expires_in") ?? 3600),
            user: parsed
          } satisfies TokenResponse);
          router.replace("/dashboard");
          return;
        }

        const response = await authApi.oauthCallback(provider, search);
        if (cancelled) return;
        setTokenResponse(response);
        router.replace("/dashboard");
      } catch (caught) {
        if (cancelled) return;
        const message =
          caught instanceof Error ? caught.message : "OAuth sign-in could not be completed.";
        setError(message);
      }
    }

    void completeOAuth();
    return () => {
      cancelled = true;
    };
  }, [provider, router, search, searchParams, setTokenResponse]);

  return (
    <AuthFormShell
      title="Completing sign-in"
      description="Finalizing the OAuth response from the backend."
      footer={
        <Link href="/login" className="font-medium text-primary hover:underline">
          Back to log in
        </Link>
      }
    >
      {error ? (
        <div className="space-y-4">
          <p className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </p>
          <Button className="w-full" asChild>
            <Link href="/login">Try again</Link>
          </Button>
        </div>
      ) : (
        <div className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Signing you in
        </div>
      )}
    </AuthFormShell>
  );
}
