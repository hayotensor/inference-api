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
import { apiErrorMessage, authApi } from "@/lib/api";
import { AuthGate } from "@/lib/auth";

const signupSchema = z.object({
  full_name: z.string().max(120).optional(),
  email: z.string().email(),
  password: z.string().min(8, "Password must be at least 8 characters")
});

type SignupForm = z.infer<typeof signupSchema>;

export default function SignupPage() {
  const router = useRouter();
  const form = useForm<SignupForm>({
    resolver: zodResolver(signupSchema),
    defaultValues: { full_name: "", email: "", password: "" }
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
              ...values,
              full_name: values.full_name || null
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
          <Button className="w-full" type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? <Loader2 className="animate-spin" /> : <ArrowRight />}
            Create account
          </Button>
        </form>
      </AuthFormShell>
    </AuthGate>
  );
}
