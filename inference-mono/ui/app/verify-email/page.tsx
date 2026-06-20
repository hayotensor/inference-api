"use client";

import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, Loader2, Mail } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { AuthFormShell } from "@/components/auth-form-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { apiErrorMessage, authApi } from "@/lib/api";

const verifySchema = z.object({ token: z.string().min(1, "Token is required") });
const requestSchema = z.object({ email: z.string().email() });

export default function VerifyEmailPage() {
  const verifyForm = useForm<z.infer<typeof verifySchema>>({
    resolver: zodResolver(verifySchema),
    defaultValues: { token: "" }
  });
  const requestForm = useForm<z.infer<typeof requestSchema>>({
    resolver: zodResolver(requestSchema),
    defaultValues: { email: "" }
  });

  const verifyMutation = useMutation({
    mutationFn: ({ token }: z.infer<typeof verifySchema>) => authApi.verifyEmail(token),
    onSuccess: () => toast.success("Email verified"),
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const requestMutation = useMutation({
    mutationFn: ({ email }: z.infer<typeof requestSchema>) =>
      authApi.requestVerifyEmail(email),
    onSuccess: (response) => toast.success(response.detail),
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    if (token) verifyForm.setValue("token", token);
  }, [verifyForm]);

  return (
    <AuthFormShell
      title="Verify email"
      description="Submit the verification token or request a new email."
      footer={
        <Link href="/login" className="font-medium text-primary hover:underline">
          Back to log in
        </Link>
      }
    >
      <form
        className="space-y-4"
        onSubmit={verifyForm.handleSubmit((values) => verifyMutation.mutate(values))}
      >
        <div className="space-y-2">
          <Label htmlFor="token">Verification token</Label>
          <Input id="token" {...verifyForm.register("token")} />
        </div>
        <Button className="w-full" type="submit" disabled={verifyMutation.isPending}>
          {verifyMutation.isPending ? (
            <Loader2 className="animate-spin" />
          ) : (
            <CheckCircle2 />
          )}
          Verify email
        </Button>
      </form>

      <div className="my-5 flex items-center gap-3">
        <Separator className="flex-1" />
        <span className="text-xs text-muted-foreground">or</span>
        <Separator className="flex-1" />
      </div>

      <form
        className="space-y-4"
        onSubmit={requestForm.handleSubmit((values) => requestMutation.mutate(values))}
      >
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" {...requestForm.register("email")} />
        </div>
        <Button className="w-full" variant="outline" type="submit" disabled={requestMutation.isPending}>
          {requestMutation.isPending ? <Loader2 className="animate-spin" /> : <Mail />}
          Send verification email
        </Button>
      </form>
    </AuthFormShell>
  );
}
