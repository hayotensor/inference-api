"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { KeyRound, Loader2, Mail, Save } from "lucide-react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { AdminConfirmDialog } from "@/components/admin-common";
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

const changePasswordSchema = z
  .object({
    current_password: z.string().min(1, "Current password is required"),
    new_password: z.string().min(8, "Password must be at least 8 characters"),
    confirm_password: z.string().min(1, "Confirm your new password")
  })
  .refine((values) => values.new_password === values.confirm_password, {
    message: "Passwords don't match",
    path: ["confirm_password"]
  });

export default function SettingsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user, setUser, logout } = useAuth();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // `oauth_providers` was added to the User schema after this app shipped, so
  // a session whose `user` snapshot is still the one cached in localStorage
  // from before that (see lib/auth.tsx's staleTime-driven cache) can hand us
  // a `user` with no `oauth_providers` field at all -- guard both reads.
  const oauthLocked = Boolean(user?.oauth_providers?.length);
  const profileForm = useForm<z.infer<typeof profileSchema>>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      full_name: user?.full_name ?? "",
      email: user?.email ?? ""
    }
  });
  useEffect(() => {
    if (!user) return;
    profileForm.reset({
      full_name: user.full_name ?? "",
      email: user.email
    });
  }, [profileForm, user]);

  const updateMutation = useMutation({
    mutationFn: userApi.update,
    // Trust the PATCH response itself -- it is the server's own record of
    // what was just saved -- instead of re-fetching (which could race with
    // a stale cache read and show the user their edit was "lost").
    onSuccess: (data) => {
      toast.success("Profile updated");
      setUser(data);
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const changePasswordForm = useForm<z.infer<typeof changePasswordSchema>>({
    resolver: zodResolver(changePasswordSchema),
    defaultValues: { current_password: "", new_password: "", confirm_password: "" }
  });

  const changePasswordMutation = useMutation({
    mutationFn: authApi.changePassword,
    // A successful change bumps the account's token_version server-side, which
    // invalidates the very access token this request used to authenticate --
    // same guarantee /auth/reset-password gives. There is no session left to
    // keep, so send the user back through login instead of leaving them on a
    // page whose next authenticated call will just 401.
    onSuccess: async () => {
      toast.success("Password changed. Please log in again.");
      changePasswordForm.reset();
      await logout();
      router.replace("/login");
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const requestVerifyMutation = useMutation({
    mutationFn: () => authApi.requestVerifyEmail(profileForm.getValues("email") ?? ""),
    onSuccess: (response) => toast.success(response.detail),
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const deleteMutation = useMutation({
    mutationFn: userApi.delete,
    onSuccess: async () => {
      toast.success("Account deleted");
      setConfirmingDelete(false);
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
        description="Review and update profile."
      />

      <div className="grid gap-4 lg:max-w-2xl">
        <Card>
          <CardHeader>
            <CardTitle>Profile</CardTitle>
            <CardDescription>Update profile.</CardDescription>
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
                  <Input
                    id="email"
                    type="email"
                    {...profileForm.register("email", { disabled: oauthLocked })}
                  />
                  {oauthLocked ? (
                    <p className="text-xs text-muted-foreground">
                      Managed by your {user?.oauth_providers?.join(", ") ?? "OAuth"} sign-in and can&apos;t be
                      changed here.
                    </p>
                  ) : null}
                </div>
              </div>
              <div className="flex flex-col gap-2 sm:flex-row">
                <Button type="submit" disabled={updateMutation.isPending}>
                  {updateMutation.isPending ? <Loader2 className="animate-spin" /> : <Save />}
                  Save profile
                </Button>
                {/* OAuth accounts (Google/Apple) come back from the backend
                    with is_verified already true -- there's no verification
                    email to send. Beyond being pointless, the button was
                    actively broken for them: the email input is disabled via
                    react-hook-form's `disabled` option above, which excludes
                    it from getValues(), so this fired requestVerifyEmail("")
                    and the API 422'd on the empty address.

                    Phone-only and wallet-only accounts have the same problem
                    from the other direction: `email` is a synthetic
                    placeholder (see `has_verifiable_email`) with no real
                    inbox behind it, so there's nothing to verify -- for
                    wallet accounts the backend used to still try to send to
                    it. `!== false` (not `Boolean(...)`) so a `user` snapshot
                    cached before this field existed defaults to showing the
                    button rather than hiding it, same reasoning as
                    `oauthLocked` above. */}
                {!oauthLocked && !user?.is_verified && user?.has_verifiable_email !== false ? (
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
                ) : null}
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

        {!oauthLocked ? (
          <Card>
            <CardHeader>
              <CardTitle>Change password</CardTitle>
              <CardDescription>You&apos;ll be signed out on other devices and asked to log in again.</CardDescription>
            </CardHeader>
            <CardContent>
              <form
                className="space-y-4"
                onSubmit={changePasswordForm.handleSubmit((values) =>
                  changePasswordMutation.mutate(values)
                )}
              >
                <div className="space-y-2">
                  <Label htmlFor="current_password">Current password</Label>
                  <Input
                    id="current_password"
                    type="password"
                    autoComplete="current-password"
                    {...changePasswordForm.register("current_password")}
                  />
                  {changePasswordForm.formState.errors.current_password ? (
                    <p className="text-xs text-destructive">
                      {changePasswordForm.formState.errors.current_password.message}
                    </p>
                  ) : null}
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="new_password">New password</Label>
                    <Input
                      id="new_password"
                      type="password"
                      autoComplete="new-password"
                      {...changePasswordForm.register("new_password")}
                    />
                    {changePasswordForm.formState.errors.new_password ? (
                      <p className="text-xs text-destructive">
                        {changePasswordForm.formState.errors.new_password.message}
                      </p>
                    ) : null}
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="confirm_password">Confirm new password</Label>
                    <Input
                      id="confirm_password"
                      type="password"
                      autoComplete="new-password"
                      {...changePasswordForm.register("confirm_password")}
                    />
                    {changePasswordForm.formState.errors.confirm_password ? (
                      <p className="text-xs text-destructive">
                        {changePasswordForm.formState.errors.confirm_password.message}
                      </p>
                    ) : null}
                  </div>
                </div>
                <Button type="submit" disabled={changePasswordMutation.isPending}>
                  {changePasswordMutation.isPending ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <KeyRound />
                  )}
                  Change password
                </Button>
              </form>
            </CardContent>
          </Card>
        ) : null}

        <Button
          type="button"
          variant="link"
          className="h-auto justify-self-start px-0 py-2 font-normal text-muted-foreground [.light_&]:text-stone [.light_&]:hover:text-muted-foreground"
          disabled={deleteMutation.isPending}
          onClick={() => setConfirmingDelete(true)}
        >
          Delete account
        </Button>
      </div>

      <AdminConfirmDialog
        open={confirmingDelete}
        onOpenChange={setConfirmingDelete}
        title="Delete this account?"
        description="This permanently deletes your account and cannot be undone."
        confirmLabel="Delete account"
        pending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />
    </>
  );
}
