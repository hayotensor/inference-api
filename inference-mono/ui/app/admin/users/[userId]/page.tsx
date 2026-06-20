"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, Power, RotateCcw, Save, Trash2, WalletCards } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  AdminConfirmDialog,
  AdminPageHeader,
  FieldGrid,
  LabeledControl,
  ReadOnlyField,
  hasPermission
} from "@/components/admin-common";
import { useAdmin } from "@/components/admin-shell";
import { ErrorState, LoadingState } from "@/components/data-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import { adminApi, apiErrorMessage } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import type { AdminRole, AdminUserDetail, ApiKeyRead } from "@/lib/types";

const profileSchema = z.object({
  email: z.string().email(),
  full_name: z.string().max(255).optional(),
  phone_number: z.string().max(32).optional(),
  is_verified: z.enum(["true", "false"])
});

const adjustmentSchema = z.object({
  amount: z.coerce.number().int().refine((value) => value !== 0, "Amount cannot be zero"),
  reason: z.string().min(1).max(512)
});

type ProfileForm = z.infer<typeof profileSchema>;
type AdjustmentForm = z.infer<typeof adjustmentSchema>;

type ConfirmAction =
  | { type: "disable"; user: AdminUserDetail }
  | { type: "enable"; user: AdminUserDetail }
  | { type: "sessions"; user: AdminUserDetail }
  | { type: "api-key"; apiKey: ApiKeyRead }
  | { type: "role"; role: AdminRole }
  | { type: "subscription"; user: AdminUserDetail }
  | { type: "wallet"; user: AdminUserDetail };

const tabs = [
  "Profile",
  "Roles",
  "API keys",
  "Billing",
  "Usage",
  "Wallet"
] as const;

export default function AdminUserDetailPage() {
  const params = useParams<{ userId: string }>();
  const userId = params.userId;
  const admin = useAdmin();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = React.useState<(typeof tabs)[number]>("Profile");
  const [confirm, setConfirm] = React.useState<ConfirmAction | null>(null);
  const [selectedRoleId, setSelectedRoleId] = React.useState("");

  const canWriteUsers = hasPermission(admin, "users.write");
  const canDisable = hasPermission(admin, "users.disable");
  const canRevokeSessions = hasPermission(admin, "users.sessions.revoke");
  const canManageRoles = hasPermission(admin, "admins.write");
  const canReadApiKeys = hasPermission(admin, "api_keys.read");
  const canRevokeApiKeys = hasPermission(admin, "api_keys.revoke");
  const canReadBilling = hasPermission(admin, "billing.read");
  const canCancelSubscriptions = hasPermission(admin, "billing.subscriptions.write");
  const canReadUsage = hasPermission(admin, "usage.read");
  const canWriteUsage = hasPermission(admin, "usage.write");
  const canReadWallet = hasPermission(admin, "wallets.read");
  const canWriteWallet = hasPermission(admin, "wallets.write");

  const userQuery = useQuery({
    queryKey: ["admin", "users", userId],
    queryFn: ({ signal }) => adminApi.user(userId, { signal })
  });
  const rolesQuery = useQuery({
    queryKey: ["admin", "roles"],
    queryFn: adminApi.roles,
    enabled: canManageRoles
  });
  const apiKeysQuery = useQuery({
    queryKey: ["admin", "users", userId, "api-keys"],
    queryFn: ({ signal }) =>
      adminApi.userApiKeys(userId, { limit: 100, sort: "-created_at" }, { signal }),
    enabled: canReadApiKeys
  });
  const billingQuery = useQuery({
    queryKey: ["admin", "users", userId, "billing"],
    queryFn: ({ signal }) => adminApi.userBilling(userId, { signal }),
    enabled: canReadBilling
  });
  const usageQuery = useQuery({
    queryKey: ["admin", "users", userId, "usage"],
    queryFn: ({ signal }) => adminApi.userUsage(userId, { signal }),
    enabled: canReadUsage
  });
  const walletQuery = useQuery({
    queryKey: ["admin", "users", userId, "wallet"],
    queryFn: ({ signal }) => adminApi.userWallet(userId, { signal }),
    enabled: canReadWallet
  });

  const profileForm = useForm<ProfileForm>({
    resolver: zodResolver(profileSchema),
    values: userQuery.data
      ? {
          email: userQuery.data.email,
          full_name: userQuery.data.full_name ?? "",
          phone_number: userQuery.data.phone_number ?? "",
          is_verified: String(userQuery.data.is_verified) as "true" | "false"
        }
      : undefined
  });

  const adjustmentForm = useForm<AdjustmentForm>({
    resolver: zodResolver(adjustmentSchema),
    defaultValues: { amount: 0, reason: "" }
  });

  const invalidateUser = async () => {
    await queryClient.invalidateQueries({ queryKey: ["admin", "users", userId] });
    await queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
  };

  const profileMutation = useMutation({
    mutationFn: (values: ProfileForm) =>
      adminApi.updateUser(userId, {
        email: values.email,
        full_name: values.full_name || null,
        phone_number: values.phone_number || null,
        is_verified: values.is_verified === "true"
      }),
    onSuccess: async () => {
      toast.success("User updated");
      await invalidateUser();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const disableMutation = useMutation({
    mutationFn: adminApi.disableUser,
    onSuccess: async () => {
      toast.success("User disabled");
      setConfirm(null);
      await invalidateUser();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const enableMutation = useMutation({
    mutationFn: adminApi.enableUser,
    onSuccess: async () => {
      toast.success("User enabled");
      setConfirm(null);
      await invalidateUser();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const sessionsMutation = useMutation({
    mutationFn: adminApi.revokeUserSessions,
    onSuccess: async (result) => {
      toast.success(`Revoked ${result.revoked_refresh_tokens} refresh tokens`);
      setConfirm(null);
      await invalidateUser();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const grantRoleMutation = useMutation({
    mutationFn: (roleId: string) => adminApi.grantRole(userId, roleId),
    onSuccess: async () => {
      toast.success("Role assigned");
      setSelectedRoleId("");
      await invalidateUser();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const revokeRoleMutation = useMutation({
    mutationFn: (roleId: string) => adminApi.revokeRole(userId, roleId),
    onSuccess: async () => {
      toast.success("Role revoked");
      setConfirm(null);
      await invalidateUser();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const revokeApiKeyMutation = useMutation({
    mutationFn: adminApi.revokeApiKey,
    onSuccess: async () => {
      toast.success("API key revoked");
      setConfirm(null);
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", userId, "api-keys"] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const cancelSubscriptionMutation = useMutation({
    mutationFn: () => adminApi.cancelUserSubscription(userId, true),
    onSuccess: async () => {
      toast.success("Subscription cancellation scheduled");
      setConfirm(null);
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", userId, "billing"] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const adjustmentMutation = useMutation({
    mutationFn: (values: AdjustmentForm) => adminApi.createManualAdjustment(userId, values),
    onSuccess: async () => {
      toast.success("Manual adjustment added");
      adjustmentForm.reset();
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", userId, "usage"] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const recalculateMutation = useMutation({
    mutationFn: () => adminApi.recalculateUserUsage(userId),
    onSuccess: async () => {
      toast.success("Usage recalculated");
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", userId, "usage"] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const syncWalletMutation = useMutation({
    mutationFn: () => adminApi.syncUserWalletBalances(userId),
    onSuccess: async (response) => {
      toast.success(`Created ${response.snapshots_created} balance snapshots`);
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", userId, "wallet"] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", userId, "usage"] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const unlinkWalletMutation = useMutation({
    mutationFn: () => adminApi.unlinkUserWallet(userId),
    onSuccess: async () => {
      toast.success("Wallet unlinked");
      setConfirm(null);
      await queryClient.invalidateQueries({ queryKey: ["admin", "users", userId, "wallet"] });
      await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  if (userQuery.isLoading) return <LoadingState />;
  if (userQuery.isError) return <ErrorState error={userQuery.error} />;
  if (!userQuery.data) return null;

  const user = userQuery.data;
  const pending =
    disableMutation.isPending ||
    enableMutation.isPending ||
    sessionsMutation.isPending ||
    revokeRoleMutation.isPending ||
    revokeApiKeyMutation.isPending ||
    cancelSubscriptionMutation.isPending ||
    unlinkWalletMutation.isPending;

  return (
    <>
      <AdminPageHeader
        title={user.email}
        description={`User ID ${user.id}`}
        action={
          <Button asChild variant="outline">
            <Link href="/admin/users">Back to users</Link>
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap gap-2">
        {tabs.map((tab) => (
          <Button
            key={tab}
            variant={activeTab === tab ? "default" : "outline"}
            size="sm"
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </Button>
        ))}
      </div>

      {activeTab === "Profile" ? (
        <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
          <Card>
            <CardHeader>
              <CardTitle>Profile</CardTitle>
            </CardHeader>
            <CardContent>
              <form
                className="space-y-4"
                onSubmit={profileForm.handleSubmit((values) => profileMutation.mutate(values))}
              >
                <FieldGrid>
                  <LabeledControl label="Email">
                    <Input disabled={!canWriteUsers} {...profileForm.register("email")} />
                  </LabeledControl>
                  <LabeledControl label="Full name">
                    <Input disabled={!canWriteUsers} {...profileForm.register("full_name")} />
                  </LabeledControl>
                  <LabeledControl label="Phone number">
                    <Input disabled={!canWriteUsers} {...profileForm.register("phone_number")} />
                  </LabeledControl>
                  <LabeledControl label="Verified">
                    <NativeSelect
                      value={profileForm.watch("is_verified")}
                      onChange={(event) =>
                        profileForm.setValue(
                          "is_verified",
                          event.target.value as "true" | "false"
                        )
                      }
                      disabled={!canWriteUsers}
                    >
                      <option value="true">Verified</option>
                      <option value="false">Unverified</option>
                    </NativeSelect>
                  </LabeledControl>
                </FieldGrid>
                {canWriteUsers ? (
                  <Button type="submit" disabled={profileMutation.isPending}>
                    <Save />
                    Save profile
                  </Button>
                ) : null}
              </form>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Account actions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <ReadOnlyField
                label="Status"
                value={
                  <Badge variant={user.is_active ? "success" : "destructive"}>
                    {user.is_active ? "active" : "disabled"}
                  </Badge>
                }
              />
              <ReadOnlyField label="Created" value={formatDateTime(user.created_at)} />
              <ReadOnlyField label="Updated" value={formatDateTime(user.updated_at)} />
              {canRevokeSessions ? (
                <Button
                  variant="outline"
                  className="w-full justify-start"
                  onClick={() => setConfirm({ type: "sessions", user })}
                >
                  <RotateCcw />
                  Revoke sessions
                </Button>
              ) : null}
              {canDisable ? (
                <Button
                  variant={user.is_active ? "destructive" : "outline"}
                  className="w-full justify-start"
                  onClick={() => setConfirm({ type: user.is_active ? "disable" : "enable", user })}
                >
                  <Power />
                  {user.is_active ? "Disable user" : "Enable user"}
                </Button>
              ) : null}
            </CardContent>
          </Card>
        </div>
      ) : null}

      {activeTab === "Roles" ? (
        <Card>
          <CardHeader>
            <CardTitle>Admin roles</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {user.roles.length > 0 ? (
                user.roles.map((role) => (
                  <Badge key={role.id} variant="outline" className="gap-2">
                    {role.name}
                    {canManageRoles ? (
                      <button
                        className="text-muted-foreground hover:text-destructive"
                        onClick={() => setConfirm({ type: "role", role })}
                      >
                        remove
                      </button>
                    ) : null}
                  </Badge>
                ))
              ) : (
                <p className="text-sm text-muted-foreground">No active admin roles.</p>
              )}
            </div>
            {canManageRoles ? (
              <div className="flex flex-col gap-2 sm:flex-row">
                <NativeSelect
                  className="sm:max-w-xs"
                  value={selectedRoleId}
                  onChange={(event) => setSelectedRoleId(event.target.value)}
                >
                  <option value="">Select role</option>
                  {rolesQuery.data?.map((role) => (
                    <option key={role.id} value={role.id}>
                      {role.name}
                    </option>
                  ))}
                </NativeSelect>
                <Button
                  disabled={!selectedRoleId || grantRoleMutation.isPending}
                  onClick={() => grantRoleMutation.mutate(selectedRoleId)}
                >
                  <CheckCircle2 />
                  Assign role
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {activeTab === "API keys" ? (
        <Card>
          <CardHeader>
            <CardTitle>API keys</CardTitle>
          </CardHeader>
          <CardContent>
            {!canReadApiKeys ? <p className="text-sm text-muted-foreground">No access.</p> : null}
            {apiKeysQuery.isLoading ? <LoadingState /> : null}
            {apiKeysQuery.data ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Key</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last used</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {apiKeysQuery.data.data.map((apiKey) => (
                    <TableRow key={apiKey.id}>
                      <TableCell>{apiKey.name}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {apiKey.prefix}...{apiKey.last_four}
                      </TableCell>
                      <TableCell>
                        <Badge variant={apiKey.revoked_at ? "destructive" : "success"}>
                          {apiKey.revoked_at ? "revoked" : "active"}
                        </Badge>
                      </TableCell>
                      <TableCell>{formatDateTime(apiKey.last_used_at)}</TableCell>
                      <TableCell className="text-right">
                        {canRevokeApiKeys ? (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={Boolean(apiKey.revoked_at)}
                            onClick={() => setConfirm({ type: "api-key", apiKey })}
                          >
                            <Trash2 />
                            Revoke
                          </Button>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {activeTab === "Billing" ? (
        <Card>
          <CardHeader>
            <CardTitle>Billing</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {!canReadBilling ? <p className="text-sm text-muted-foreground">No access.</p> : null}
            {billingQuery.data ? (
              <>
                <FieldGrid>
                  <ReadOnlyField label="Status" value={billingQuery.data.status} />
                  <ReadOnlyField label="Plan" value={billingQuery.data.plan_name} />
                  <ReadOnlyField label="Stripe customer" value={billingQuery.data.stripe_customer_id} />
                  <ReadOnlyField label="Subscription" value={billingQuery.data.stripe_subscription_id} />
                  <ReadOnlyField label="Period start" value={formatDateTime(billingQuery.data.current_period_start)} />
                  <ReadOnlyField label="Period end" value={formatDateTime(billingQuery.data.current_period_end)} />
                </FieldGrid>
                {canCancelSubscriptions && billingQuery.data.stripe_subscription_id ? (
                  <Button variant="destructive" onClick={() => setConfirm({ type: "subscription", user })}>
                    Cancel subscription
                  </Button>
                ) : null}
              </>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {activeTab === "Usage" ? (
        <Card>
          <CardHeader>
            <CardTitle>Usage</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {!canReadUsage ? <p className="text-sm text-muted-foreground">No access.</p> : null}
            {usageQuery.data ? (
              <FieldGrid>
                <ReadOnlyField label="Total allowance" value={formatNumber(usageQuery.data.total_allowance)} />
                <ReadOnlyField label="Used tokens" value={formatNumber(usageQuery.data.used_tokens)} />
                <ReadOnlyField label="Remaining" value={formatNumber(usageQuery.data.remaining_tokens)} />
                <ReadOnlyField label="Manual adjustments" value={formatNumber(usageQuery.data.manual_allowance)} />
                <ReadOnlyField label="Period start" value={formatDateTime(usageQuery.data.period_start)} />
                <ReadOnlyField label="Period end" value={formatDateTime(usageQuery.data.period_end)} />
              </FieldGrid>
            ) : null}
            {canWriteUsage ? (
              <form
                className="grid gap-3 rounded-lg border bg-muted/30 p-4 md:grid-cols-[180px_1fr_auto]"
                onSubmit={adjustmentForm.handleSubmit((values) => adjustmentMutation.mutate(values))}
              >
                <LabeledControl label="Amount">
                  <Input type="number" {...adjustmentForm.register("amount")} />
                </LabeledControl>
                <LabeledControl label="Reason">
                  <Input {...adjustmentForm.register("reason")} />
                </LabeledControl>
                <div className="flex items-end gap-2">
                  <Button type="submit" disabled={adjustmentMutation.isPending}>
                    Add adjustment
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={recalculateMutation.isPending}
                    onClick={() => recalculateMutation.mutate()}
                  >
                    Recalculate
                  </Button>
                </div>
              </form>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {activeTab === "Wallet" ? (
        <Card>
          <CardHeader>
            <CardTitle>EVM wallet</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {!canReadWallet ? <p className="text-sm text-muted-foreground">No access.</p> : null}
            {walletQuery.data ? (
              <FieldGrid>
                <ReadOnlyField label="Address" value={walletQuery.data.address} />
                <ReadOnlyField label="Verified" value={formatDateTime(walletQuery.data.verified_at)} />
                <ReadOnlyField label="Last sync" value={formatDateTime(walletQuery.data.last_balance_sync_at)} />
              </FieldGrid>
            ) : null}
            {canWriteWallet ? (
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  disabled={syncWalletMutation.isPending || !walletQuery.data?.address}
                  onClick={() => syncWalletMutation.mutate()}
                >
                  <WalletCards />
                  Sync balances
                </Button>
                <Button
                  variant="destructive"
                  disabled={!walletQuery.data?.address}
                  onClick={() => setConfirm({ type: "wallet", user })}
                >
                  Unlink wallet
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <AdminConfirmDialog
        open={Boolean(confirm)}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        pending={pending}
        destructive={confirm?.type !== "enable"}
        title={confirmTitle(confirm)}
        description={confirmDescription(confirm)}
        confirmLabel={confirmLabel(confirm)}
        onConfirm={() => {
          if (!confirm) return;
          if (confirm.type === "disable") disableMutation.mutate(confirm.user.id);
          if (confirm.type === "enable") enableMutation.mutate(confirm.user.id);
          if (confirm.type === "sessions") sessionsMutation.mutate(confirm.user.id);
          if (confirm.type === "role") revokeRoleMutation.mutate(confirm.role.id);
          if (confirm.type === "api-key") revokeApiKeyMutation.mutate(confirm.apiKey.id);
          if (confirm.type === "subscription") cancelSubscriptionMutation.mutate();
          if (confirm.type === "wallet") unlinkWalletMutation.mutate();
        }}
      />
    </>
  );
}

function confirmTitle(confirm: ConfirmAction | null) {
  if (!confirm) return "Confirm action";
  const titles = {
    disable: "Disable this user?",
    enable: "Enable this user?",
    sessions: "Revoke all user sessions?",
    role: "Revoke this admin role?",
    "api-key": "Revoke this API key?",
    subscription: "Cancel this subscription?",
    wallet: "Unlink this wallet?"
  };
  return titles[confirm.type];
}

function confirmDescription(confirm: ConfirmAction | null) {
  if (!confirm) return "";
  const descriptions = {
    disable: "The user will lose access and active sessions will be revoked.",
    enable: "The user will regain access to authenticated routes.",
    sessions: "The user will need to log in again on all devices.",
    role: "The role assignment will be revoked immediately.",
    "api-key": "The API key will stop authenticating requests.",
    subscription: "The backend will request subscription cancellation through Stripe.",
    wallet: "The active wallet link will be removed while balance snapshots remain."
  };
  return descriptions[confirm.type];
}

function confirmLabel(confirm: ConfirmAction | null) {
  if (!confirm) return "Confirm";
  const labels = {
    disable: "Disable user",
    enable: "Enable user",
    sessions: "Revoke sessions",
    role: "Revoke role",
    "api-key": "Revoke API key",
    subscription: "Cancel subscription",
    wallet: "Unlink wallet"
  };
  return labels[confirm.type];
}
