"use client";

import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Mail } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { AuthFormShell } from "@/components/auth-form-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiErrorMessage, authApi } from "@/lib/api";
import { AuthGate } from "@/lib/auth";

const schema = z.object({ email: z.string().email() });

export default function ForgotPasswordPage() {
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { email: "" }
  });
  const mutation = useMutation({
    mutationFn: ({ email }: z.infer<typeof schema>) => authApi.forgotPassword(email),
    onSuccess: (response) => toast.success(response.detail),
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  return (
    <AuthGate guestOnly>
      <AuthFormShell
        title="Reset password"
        description="Request a reset token from the backend."
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
            <Label htmlFor="email">Email</Label>
            <Input id="email" type="email" autoComplete="email" {...form.register("email")} />
          </div>
          <Button className="w-full" type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? <Loader2 className="animate-spin" /> : <Mail />}
            Send reset email
          </Button>
        </form>
      </AuthFormShell>
    </AuthGate>
  );
}
