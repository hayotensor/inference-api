"use client";

import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, Loader2 } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { AuthFormShell } from "@/components/auth-form-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiErrorMessage, authApi } from "@/lib/api";
import { AuthGate } from "@/lib/auth";

const schema = z.object({
  token: z.string().min(1, "Reset token is required"),
  password: z.string().min(8, "Password must be at least 8 characters")
});

export default function ResetPasswordPage() {
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { token: "", password: "" }
  });
  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    if (token) form.setValue("token", token);
  }, [form]);
  const mutation = useMutation({
    mutationFn: authApi.resetPassword,
    onSuccess: (response) => toast.success(response.detail),
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  return (
    <AuthGate guestOnly>
      <AuthFormShell
        title="Set new password"
        description="Submit the reset token and your new password."
        footer={
          <Link href="/login" className="font-medium text-primary hover:underline">
            Back to log in
          </Link>
        }
      >
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) => mutation.mutate(values))}
        >
          <div className="space-y-2">
            <Label htmlFor="token">Reset token</Label>
            <Input id="token" {...form.register("token")} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">New password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              {...form.register("password")}
            />
          </div>
          <Button className="w-full" type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? <Loader2 className="animate-spin" /> : <KeyRound />}
            Reset password
          </Button>
        </form>
      </AuthFormShell>
    </AuthGate>
  );
}
