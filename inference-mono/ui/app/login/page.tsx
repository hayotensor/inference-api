"use client";

import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowRight, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { AuthFormShell } from "@/components/auth-form-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { apiErrorMessage, authApi } from "@/lib/api";
import { AuthGate, useAuth } from "@/lib/auth";

const loginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1, "Password is required")
});

type LoginForm = z.infer<typeof loginSchema>;

export default function LoginPage() {
  const router = useRouter();
  const { setTokenResponse } = useAuth();
  const form = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" }
  });

  const mutation = useMutation({
    mutationFn: authApi.login,
    onSuccess: (response) => {
      setTokenResponse(response);
      toast.success("Logged in");
      const next =
        new URLSearchParams(window.location.search).get("next") || "/dashboard";
      router.replace(next);
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  return (
    <AuthGate guestOnly>
      <AuthFormShell
        title="Log in"
        description="Use your email and password, OAuth provider, or phone OTP."
        footer={
          <>
            Need an account?{" "}
            <Link href="/signup" className="font-medium text-primary hover:underline">
              Sign up
            </Link>
          </>
        }
      >
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
          <Button variant="outline" asChild>
            <a href={authApi.oauthLoginUrl("google")}>Continue with Google</a>
          </Button>
          <Button variant="outline" asChild>
            <a href={authApi.oauthLoginUrl("apple")}>Continue with Apple</a>
          </Button>
          <Button variant="secondary" asChild>
            <Link href="/phone-login">Continue with phone</Link>
          </Button>
        </div>

        <div className="mt-5 text-center text-sm">
          <Link href="/forgot-password" className="text-primary hover:underline">
            Forgot password?
          </Link>
        </div>
      </AuthFormShell>
    </AuthGate>
  );
}
