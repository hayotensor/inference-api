"use client";

import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, MessageSquareText, Phone } from "lucide-react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { AuthFormShell } from "@/components/auth-form-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiErrorMessage, authApi } from "@/lib/api";
import { AuthGate, useAuth } from "@/lib/auth";

const schema = z.object({
  phone_number: z.string().min(8, "Phone number is required"),
  code: z.string().length(6, "Code must be 6 digits").optional(),
  full_name: z.string().optional()
});

type PhoneForm = z.infer<typeof schema>;

export default function PhoneLoginPage() {
  const router = useRouter();
  const { setTokenResponse } = useAuth();
  const form = useForm<PhoneForm>({
    resolver: zodResolver(schema),
    defaultValues: { phone_number: "", code: "", full_name: "" }
  });
  const requestMutation = useMutation({
    mutationFn: (phone_number: string) =>
      authApi.requestPhoneOtp({ phone_number, purpose: "login" }),
    onSuccess: (response) => toast.success(response.detail),
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const verifyMutation = useMutation({
    mutationFn: (values: PhoneForm) =>
      authApi.verifyPhoneOtp({
        phone_number: values.phone_number,
        code: values.code ?? "",
        full_name: values.full_name || undefined
      }),
    onSuccess: (response) => {
      setTokenResponse(response);
      toast.success("Logged in with phone");
      router.replace("/dashboard");
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  return (
    <AuthGate guestOnly>
      <AuthFormShell
        title="Phone login"
        description="Request an OTP, then verify through the backend phone auth API."
        footer={
          <Link href="/login" className="font-medium text-primary hover:underline">
            Use email instead
          </Link>
        }
      >
        <form
          className="space-y-4"
          onSubmit={form.handleSubmit((values) => verifyMutation.mutate(values))}
        >
          <div className="space-y-2">
            <Label htmlFor="phone_number">Phone number</Label>
            <Input
              id="phone_number"
              type="tel"
              autoComplete="tel"
              placeholder="+15551234567"
              {...form.register("phone_number")}
            />
          </div>
          <Button
            className="w-full"
            type="button"
            variant="outline"
            disabled={requestMutation.isPending}
            onClick={() => requestMutation.mutate(form.getValues("phone_number"))}
          >
            {requestMutation.isPending ? <Loader2 className="animate-spin" /> : <Phone />}
            Send OTP
          </Button>
          <div className="space-y-2">
            <Label htmlFor="code">Code</Label>
            <Input
              id="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              {...form.register("code")}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="full_name">Name for new phone accounts</Label>
            <Input id="full_name" autoComplete="name" {...form.register("full_name")} />
          </div>
          <Button className="w-full" type="submit" disabled={verifyMutation.isPending}>
            {verifyMutation.isPending ? (
              <Loader2 className="animate-spin" />
            ) : (
              <MessageSquareText />
            )}
            Verify and log in
          </Button>
        </form>
      </AuthFormShell>
    </AuthGate>
  );
}
