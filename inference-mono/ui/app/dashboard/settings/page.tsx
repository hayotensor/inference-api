"use client";

import { useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Mail, Phone, Save, Trash2, Unlink } from "lucide-react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { apiErrorMessage, authApi, userApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDateTime } from "@/lib/format";

const profileSchema = z.object({
  full_name: z.string().max(120).optional(),
  email: z.string().email().optional()
});

const phoneSchema = z.object({
  phone_number: z.string().min(8, "Phone number is required"),
  code: z.string().length(6, "Code must be 6 digits").optional()
});

export default function SettingsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user, refreshUser, logout } = useAuth();
  const profileForm = useForm<z.infer<typeof profileSchema>>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      full_name: user?.full_name ?? "",
      email: user?.email ?? ""
    }
  });
  const phoneForm = useForm<z.infer<typeof phoneSchema>>({
    resolver: zodResolver(phoneSchema),
    defaultValues: {
      phone_number: user?.phone_number ?? "",
      code: ""
    }
  });

  useEffect(() => {
    if (!user) return;
    profileForm.reset({
      full_name: user.full_name ?? "",
      email: user.email
    });
    phoneForm.reset({
      phone_number: user.phone_number ?? "",
      code: ""
    });
  }, [phoneForm, profileForm, user]);

  const updateMutation = useMutation({
    mutationFn: userApi.update,
    onSuccess: async () => {
      toast.success("Profile updated");
      await refreshUser();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const requestVerifyMutation = useMutation({
    mutationFn: () => authApi.requestVerifyEmail(profileForm.getValues("email") ?? ""),
    onSuccess: (response) => toast.success(response.detail),
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const requestPhoneMutation = useMutation({
    mutationFn: (phone_number: string) =>
      authApi.requestPhoneOtp({ phone_number, purpose: "link" }),
    onSuccess: (response) => toast.success(response.detail),
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const linkPhoneMutation = useMutation({
    mutationFn: authApi.linkPhone,
    onSuccess: async () => {
      toast.success("Phone linked");
      await refreshUser();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const unlinkPhoneMutation = useMutation({
    mutationFn: authApi.unlinkPhone,
    onSuccess: async () => {
      toast.success("Phone unlinked");
      await refreshUser();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const deleteMutation = useMutation({
    mutationFn: userApi.delete,
    onSuccess: async () => {
      toast.success("Account deleted");
      await logout();
      queryClient.clear();
      router.replace("/");
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  return (
    <>
      <DashboardPageHeader
        title="Settings"
        description="Account updates are sent to the existing user and auth endpoints."
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_0.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>Profile</CardTitle>
            <CardDescription>Update account fields through `/me`.</CardDescription>
          </CardHeader>
          <CardContent>
            <form
              className="space-y-4"
              onSubmit={profileForm.handleSubmit((values) => updateMutation.mutate(values))}
            >
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="full_name">Name</Label>
                  <Input id="full_name" {...profileForm.register("full_name")} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" type="email" {...profileForm.register("email")} />
                </div>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Button type="submit" disabled={updateMutation.isPending}>
                  {updateMutation.isPending ? <Loader2 className="animate-spin" /> : <Save />}
                  Save profile
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => requestVerifyMutation.mutate()}
                  disabled={requestVerifyMutation.isPending}
                >
                  {requestVerifyMutation.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Mail />
                  )}
                  Send verification
                </Button>
              </div>
            </form>

            <Separator className="my-6" />

            <div className="grid gap-4 text-sm sm:grid-cols-2">
              <div className="rounded-lg border bg-muted/30 p-3">
                <p className="text-muted-foreground">Email status</p>
                <Badge className="mt-2" variant={user?.is_verified ? "success" : "warning"}>
                  {user?.is_verified ? "verified" : "unverified"}
                </Badge>
              </div>
              <div className="rounded-lg border bg-muted/30 p-3">
                <p className="text-muted-foreground">Created</p>
                <p className="mt-2 font-medium">{formatDateTime(user?.created_at)}</p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Phone</CardTitle>
            <CardDescription>Request and verify OTP codes through backend phone auth.</CardDescription>
          </CardHeader>
          <CardContent>
            <form
              className="space-y-4"
              onSubmit={phoneForm.handleSubmit((values) =>
                linkPhoneMutation.mutate({
                  phone_number: values.phone_number,
                  code: values.code ?? ""
                })
              )}
            >
              <div className="space-y-2">
                <Label htmlFor="phone_number">Phone number</Label>
                <Input id="phone_number" type="tel" {...phoneForm.register("phone_number")} />
              </div>
              <Button
                className="w-full"
                type="button"
                variant="outline"
                disabled={requestPhoneMutation.isPending}
                onClick={() => requestPhoneMutation.mutate(phoneForm.getValues("phone_number"))}
              >
                {requestPhoneMutation.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Phone />
                )}
                Send link OTP
              </Button>
              <div className="space-y-2">
                <Label htmlFor="code">OTP code</Label>
                <Input id="code" inputMode="numeric" {...phoneForm.register("code")} />
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                <Button type="submit" disabled={linkPhoneMutation.isPending}>
                  {linkPhoneMutation.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Phone />
                  )}
                  Link phone
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={!user?.phone_number || unlinkPhoneMutation.isPending}
                  onClick={() => unlinkPhoneMutation.mutate()}
                >
                  {unlinkPhoneMutation.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Unlink />
                  )}
                  Unlink
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>

      <Card className="mt-4 border-destructive/30">
        <CardHeader>
          <CardTitle>Danger zone</CardTitle>
          <CardDescription>Deleting the account calls `DELETE /me`.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button
            variant="destructive"
            disabled={deleteMutation.isPending}
            onClick={() => {
              if (window.confirm("Delete this account? This cannot be undone.")) {
                deleteMutation.mutate();
              }
            }}
          >
            {deleteMutation.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
            Delete account
          </Button>
        </CardContent>
      </Card>
    </>
  );
}
