"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowRight, Loader2 } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { AuthFormShell } from "@/components/auth-form-shell";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiErrorMessage, authApi } from "@/lib/api";
import { AuthGate } from "@/lib/auth";

const signupSchema = z.object({
  full_name: z.string().max(120).optional(),
  email: z.string().email(),
  password: z.string().min(8, "Password must be at least 8 characters"),
  // HYP-69: affirmative opt-in to the Terms and Privacy Policy (never pre-checked).
  accept_legal: z.boolean().refine((value) => value === true, {
    message: "You must accept the Terms and Privacy Policy to create an account"
  })
});

type SignupForm = z.infer<typeof signupSchema>;

export default function SignupPage() {
  return (
    <React.Suspense fallback={null}>
      <SignupPageContent />
    </React.Suspense>
  );
}

function SignupPageContent() {
  const router = useRouter();
  // HYP-65: `?ref=<code>` on a shared referral link (talaris-inference.../signup?ref=...).
  // Read once on load and threaded through to the register call unchanged --
  // an unknown/invalid code is never an error, it just means no attribution.
  const searchParams = useSearchParams();
  const referralCode = searchParams.get("ref")?.trim() || undefined;
  const form = useForm<SignupForm>({
    resolver: zodResolver(signupSchema),
    defaultValues: { full_name: "", email: "", password: "", accept_legal: false }
  });

  const mutation = useMutation({
    mutationFn: authApi.register,
    onSuccess: () => {
      toast.success("Account created. Check your email for verification.");
      router.replace("/login");
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  return (
    <AuthGate guestOnly>
      <AuthFormShell
        title="Create account"
        description="Register through the backend auth API."
        footer={
          <>
            Already have an account?{" "}
            <Link href="/login" className="font-medium text-primary hover:underline">
              Log in
            </Link>
          </>
        }
      >
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) =>
            mutation.mutate({
              email: values.email,
              password: values.password,
              full_name: values.full_name || null,
              referral_code: referralCode
            })
          )}
        >
          <div className="space-y-2">
            <Label htmlFor="full_name">Name</Label>
            <Input id="full_name" autoComplete="name" {...form.register("full_name")} />
          </div>
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
              autoComplete="new-password"
              {...form.register("password")}
            />
            {form.formState.errors.password ? (
              <p className="text-sm text-destructive">{form.formState.errors.password.message}</p>
            ) : null}
          </div>
          <div className="space-y-2">
            <div className="flex items-start gap-3">
              <Controller
                control={form.control}
                name="accept_legal"
                render={({ field }) => (
                  <Checkbox
                    id="accept_legal"
                    className="mt-0.5"
                    checked={field.value}
                    onCheckedChange={(checked) => field.onChange(checked === true)}
                  />
                )}
              />
              <Label htmlFor="accept_legal" className="text-sm font-normal normal-case leading-relaxed tracking-normal text-muted-foreground">
                I am at least 18 and agree to the{" "}
                <Link href="/terms" target="_blank" className="text-primary hover:underline">
                  Terms of Service
                </Link>{" "}
                and have read the{" "}
                <Link href="/privacy" target="_blank" className="text-primary hover:underline">
                  Privacy Policy
                </Link>
                .
              </Label>
            </div>
            {form.formState.errors.accept_legal ? (
              <p className="text-sm text-destructive">
                {form.formState.errors.accept_legal.message}
              </p>
            ) : null}
          </div>
          <Button className="w-full" type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? <Loader2 className="animate-spin" /> : <ArrowRight />}
            Create account
          </Button>
        </form>
      </AuthFormShell>
    </AuthGate>
  );
}
