"use client";

import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowRight, Loader2, WalletCards } from "lucide-react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { useWallet } from "@/lib/wallet";
import { z } from "zod";

import { AuthFormShell } from "@/components/auth-form-shell";
import { NoWalletNotice } from "@/components/no-wallet-notice";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { apiErrorMessage, authApi } from "@/lib/api";
import { AuthGate, navigateAfterAuth, safeNextPath, useAuth } from "@/lib/auth";
import { shortenAddress } from "@/lib/format";
import { DEMO_EMAIL, DEMO_PASSWORD, UI_DEMO_MODE } from "@/lib/demo-mode";

const loginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1, "Password is required")
});

type LoginForm = z.infer<typeof loginSchema>;

export default function LoginPage() {
  const router = useRouter();
  const { setTokenResponse, ensureSsoCookie } = useAuth();
  const { address, signMessageAsync, openWallet, loading: walletLoading } = useWallet();
  const form = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" }
  });

  const onAuthenticated = async (response: Parameters<typeof setTokenResponse>[0]) => {
    setTokenResponse(response);
    toast.success("Logged in");
    const next = safeNextPath(new URLSearchParams(window.location.search).get("next"));
    // setTokenResponse mints the SSO cookie in the background; wait for it when the
    // destination hands off to another origin (chat), or that hop sees no cookie
    // and bounces straight back here.
    await ensureSsoCookie();
    navigateAfterAuth(router, next);
  };

  const mutation = useMutation({
    mutationFn: authApi.login,
    onSuccess: onAuthenticated,
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const walletLoginMutation = useMutation({
    mutationFn: async () => {
      if (!address) throw new Error("Connect a wallet first.");
      const nonce = await authApi.walletLoginNonce(address);
      const signature = await signMessageAsync({ message: nonce.message });
      return authApi.walletLoginVerify({
        address: nonce.address,
        message: nonce.message,
        signature
      });
    },
    onSuccess: onAuthenticated,
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  return (
    <AuthGate guestOnly>
      <AuthFormShell
        title="Log in"
        description="Use your email and password, continue with Google, or sign in with a browser wallet."
        footer={
          <>
            Need an account?{" "}
            <Link href="/signup" className="font-medium text-primary hover:underline">
              Sign up
            </Link>
          </>
        }
      >
        {UI_DEMO_MODE ? (
          <div className="mb-5 space-y-2 rounded-lg border p-4">
            <p className="text-sm text-muted-foreground">Explore the dashboard and admin screens with sample data.</p>
            <Button
              className="w-full"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate({ email: DEMO_EMAIL, password: DEMO_PASSWORD })}
            >
              Enter local demo
            </Button>
          </div>
        ) : null}
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
        >
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="email" {...form.register("email")} />
            {form.formState.errors.email ? (
              <p className="text-sm text-destructive">{form.formState.errors.email.message}</p>
            ) : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              {...form.register("password")}
            />
            {form.formState.errors.password ? (
              <p className="text-sm text-destructive">{form.formState.errors.password.message}</p>
            ) : null}
          </div>
          <Button className="w-full" type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? <Loader2 className="animate-spin" /> : <ArrowRight />}
            Log in
          </Button>
        </form>

        <div className="my-5 flex items-center gap-3">
          <Separator className="flex-1" />
          <span className="text-xs text-muted-foreground">or</span>
          <Separator className="flex-1" />
        </div>

        <div className="grid gap-2">
          {UI_DEMO_MODE ? (
            <Button variant="outline" disabled>Google sign-in unavailable in demo</Button>
          ) : (
            <Button variant="outline" asChild>
              <a href={authApi.oauthLoginUrl("google")}>Continue with Google</a>
            </Button>
          )}
          {address ? (
            <Button
              variant="outline"
              onClick={() => walletLoginMutation.mutate()}
              disabled={UI_DEMO_MODE || walletLoginMutation.isPending}
            >
              {walletLoginMutation.isPending ? (
                <Loader2 className="animate-spin" />
              ) : (
                <WalletCards />
              )}
              Sign in with wallet ({shortenAddress(address)})
            </Button>
          ) : (
            <Button
              variant="outline"
              onClick={openWallet}
              disabled={UI_DEMO_MODE || walletLoading}
            >
              {walletLoading ? <Loader2 className="animate-spin" /> : <WalletCards />}
              Sign in with wallet
            </Button>
          )}
        </div>

        <p className="mt-3 text-center text-xs text-muted-foreground">
          Connect an EVM wallet -- a browser extension, or a mobile/hardware wallet via
          WalletConnect -- then sign a message to prove ownership. No password is created.
        </p>

        <p className="mt-3 text-center text-xs text-muted-foreground">
          Signing in with Google or a wallet for the first time creates an account, and by
          continuing you agree to the{" "}
          <Link href="/terms" className="text-foreground underline underline-offset-4">
            Terms of Service
          </Link>{" "}
          and{" "}
          <Link href="/privacy" className="text-foreground underline underline-offset-4">
            Privacy Policy
          </Link>
          .
        </p>

        <NoWalletNotice className="mt-3" />

        <div className="mt-5 text-center text-sm">
          <Link href="/forgot-password" className="text-primary hover:underline">
            Forgot password?
          </Link>
        </div>
      </AuthFormShell>
    </AuthGate>
  );
}
