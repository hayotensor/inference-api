"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Power, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import {
  AdminConfirmDialog,
  AdminFilterBar,
  AdminPageHeader,
  AdminPagination,
  hasPermission
} from "@/components/admin-common";
import { useAdmin } from "@/components/admin-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import { adminApi, apiErrorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AdminUserSummary } from "@/lib/types";

const limit = 25;

export default function AdminUsersPage() {
  const admin = useAdmin();
  const queryClient = useQueryClient();
  const [search, setSearch] = React.useState("");
  const [status, setStatus] = React.useState("all");
  const [sort, setSort] = React.useState("-created_at");
  const [offset, setOffset] = React.useState(0);
  const [confirm, setConfirm] = React.useState<{
    action: "disable" | "enable" | "sessions";
    user: AdminUserSummary;
  } | null>(null);

  const usersQuery = useQuery({
    queryKey: ["admin", "users", { search, status, sort, offset }],
    queryFn: ({ signal }) =>
      adminApi.users({ limit, offset, search, status, sort }, { signal })
  });

  const invalidateUsers = async () => {
    await queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
  };

  const disableMutation = useMutation({
    mutationFn: adminApi.disableUser,
    onSuccess: async () => {
      toast.success("User disabled");
      setConfirm(null);
      await invalidateUsers();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const enableMutation = useMutation({
    mutationFn: adminApi.enableUser,
    onSuccess: async () => {
      toast.success("User enabled");
      setConfirm(null);
      await invalidateUsers();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const sessionsMutation = useMutation({
    mutationFn: adminApi.revokeUserSessions,
    onSuccess: async (result) => {
      toast.success(`Revoked ${result.revoked_refresh_tokens} refresh tokens`);
      setConfirm(null);
      await invalidateUsers();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const pending =
    disableMutation.isPending || enableMutation.isPending || sessionsMutation.isPending;
  const canDisable = hasPermission(admin, "users.disable");
  const canRevokeSessions = hasPermission(admin, "users.sessions.revoke");

  return (
    <>
      <AdminPageHeader
        title="Users"
        description="Search accounts, inspect user data, and perform account actions."
      />

      <AdminFilterBar
        search={search}
        onSearchChange={(value) => {
          setSearch(value);
          setOffset(0);
        }}
        status={status}
        onStatusChange={(value) => {
          setStatus(value);
          setOffset(0);
        }}
        statusOptions={[
          { value: "all", label: "All statuses" },
          { value: "active", label: "Active" },
          { value: "disabled", label: "Disabled" },
          { value: "verified", label: "Verified" },
          { value: "unverified", label: "Unverified" }
        ]}
        sort={sort}
        onSortChange={(value) => {
          setSort(value);
          setOffset(0);
        }}
        sortOptions={[
          { value: "-created_at", label: "Newest first" },
          { value: "created_at", label: "Oldest first" },
          { value: "email", label: "Email A-Z" },
          { value: "-email", label: "Email Z-A" }
        ]}
        onRefresh={() => void usersQuery.refetch()}
      />

      {usersQuery.isLoading ? <LoadingState /> : null}
      {usersQuery.isError ? <ErrorState error={usersQuery.error} /> : null}
      {usersQuery.data?.data.length === 0 ? (
        <EmptyState title="No users found" description="Adjust the search or filters." />
      ) : null}

      {usersQuery.data && usersQuery.data.data.length > 0 ? (
        <>
          <div className="rounded-lg border bg-background">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Verification</TableHead>
                  <TableHead>API keys</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usersQuery.data.data.map((user) => (
                  <TableRow key={user.id}>
                    <TableCell>
                      <div className="font-medium">{user.email}</div>
                      <div className="text-xs text-muted-foreground">
                        {user.full_name || "No name"} {user.phone_number ? `· ${user.phone_number}` : ""}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={user.is_active ? "success" : "destructive"}>
                        {user.is_active ? "active" : "disabled"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={user.is_verified ? "success" : "outline"}>
                        {user.is_verified ? "verified" : "unverified"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {user.active_api_key_count}/{user.api_key_count}
                    </TableCell>
                    <TableCell>{formatDateTime(user.created_at)}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-2">
                        <Button asChild variant="outline" size="sm">
                          <Link href={`/admin/users/${user.id}`}>
                            <Eye />
                            View
                          </Link>
                        </Button>
                        {canRevokeSessions ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setConfirm({ action: "sessions", user })}
                          >
                            <RotateCcw />
                            Sessions
                          </Button>
                        ) : null}
                        {canDisable ? (
                          <Button
                            variant={user.is_active ? "destructive" : "outline"}
                            size="sm"
                            onClick={() =>
                              setConfirm({
                                action: user.is_active ? "disable" : "enable",
                                user
                              })
                            }
                          >
                            <Power />
                            {user.is_active ? "Disable" : "Enable"}
                          </Button>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <AdminPagination
            total={usersQuery.data.total}
            limit={usersQuery.data.limit}
            offset={usersQuery.data.offset}
            onOffsetChange={setOffset}
          />
        </>
      ) : null}

      <AdminConfirmDialog
        open={Boolean(confirm)}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        pending={pending}
        destructive={confirm?.action !== "enable"}
        title={
          confirm?.action === "sessions"
            ? "Revoke all user sessions?"
            : confirm?.action === "enable"
              ? "Enable this user?"
              : "Disable this user?"
        }
        description={
          confirm?.action === "sessions"
            ? "The user will need to log in again on all devices."
            : confirm?.action === "enable"
              ? "The user will regain access to authenticated routes."
              : "The user will lose access and active sessions will be revoked."
        }
        confirmLabel={
          confirm?.action === "sessions"
            ? "Revoke sessions"
            : confirm?.action === "enable"
              ? "Enable user"
              : "Disable user"
        }
        onConfirm={() => {
          if (!confirm) return;
          if (confirm.action === "sessions") sessionsMutation.mutate(confirm.user.id);
          if (confirm.action === "enable") enableMutation.mutate(confirm.user.id);
          if (confirm.action === "disable") disableMutation.mutate(confirm.user.id);
        }}
      />
    </>
  );
}
