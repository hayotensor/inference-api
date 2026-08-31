"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Loader2, Mail, Save, Trash2 } from "lucide-react";
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

export default function SettingsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { user, setUser, logout } = useAuth();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const oauthLocked = Boolean(user?.oauth_providers.length);
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
        description="Account updates are sent to the existing user and auth endpoints."
      />

      <div className="grid gap-4 lg:max-w-2xl">
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
                  <Input
                    id="email"
                    type="email"
                    {...profileForm.register("email", { disabled: oauthLocked })}
                  />
                  {oauthLocked ? (
                    <p className="text-xs text-muted-foreground">
                      Managed by your {user?.oauth_providers.join(", ")} sign-in and can&apos;t be
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
            onClick={() => setConfirmingDelete(true)}
          >
            {deleteMutation.isPending ? <Loader2 className="animate-spin" /> : <Trash2 />}
            Delete account
          </Button>
        </CardContent>
      </Card>

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
