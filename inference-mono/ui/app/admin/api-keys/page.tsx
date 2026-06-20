"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Edit, Trash2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  AdminConfirmDialog,
  AdminFilterBar,
  AdminPageHeader,
  AdminPagination,
  LabeledControl,
  hasPermission
} from "@/components/admin-common";
import { useAdmin } from "@/components/admin-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
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
import type { ApiKeyRead } from "@/lib/types";

const limit = 25;

const keySchema = z.object({
  name: z.string().min(1).max(120),
  rate_limit_per_minute: z.coerce.number().int().min(1).max(60000)
});

type KeyForm = z.infer<typeof keySchema>;

export default function AdminApiKeysPage() {
  const admin = useAdmin();
  const queryClient = useQueryClient();
  const [search, setSearch] = React.useState("");
  const [status, setStatus] = React.useState("all");
  const [sort, setSort] = React.useState("-created_at");
  const [offset, setOffset] = React.useState(0);
  const [editing, setEditing] = React.useState<ApiKeyRead | null>(null);
  const [revoking, setRevoking] = React.useState<ApiKeyRead | null>(null);
  const canUpdate = hasPermission(admin, "api_keys.write");
  const canRevoke = hasPermission(admin, "api_keys.revoke");

  const keysQuery = useQuery({
    queryKey: ["admin", "api-keys", { search, status, sort, offset }],
    queryFn: ({ signal }) =>
      adminApi.apiKeys({ limit, offset, search, status, sort }, { signal })
  });

  const form = useForm<KeyForm>({
    resolver: zodResolver(keySchema),
    values: editing
      ? {
          name: editing.name,
          rate_limit_per_minute: editing.rate_limit_per_minute
        }
      : undefined
  });

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["admin", "api-keys"] });
    await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
  };

  const updateMutation = useMutation({
    mutationFn: (values: KeyForm) =>
      editing
        ? adminApi.updateApiKey(editing.id, {
            name: values.name,
            rate_limit_per_minute: values.rate_limit_per_minute
          })
        : Promise.reject(new Error("No API key selected")),
    onSuccess: async () => {
      toast.success("API key updated");
      setEditing(null);
      await invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const revokeMutation = useMutation({
    mutationFn: adminApi.revokeApiKey,
    onSuccess: async () => {
      toast.success("API key revoked");
      setRevoking(null);
      await invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  return (
    <>
      <AdminPageHeader
        title="API keys"
        description="Search, update, and revoke user API keys. Plaintext keys are never shown."
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
          { value: "revoked", label: "Revoked" },
          { value: "expired", label: "Expired" }
        ]}
        sort={sort}
        onSortChange={(value) => {
          setSort(value);
          setOffset(0);
        }}
        sortOptions={[
          { value: "-created_at", label: "Newest first" },
          { value: "created_at", label: "Oldest first" },
          { value: "-last_used_at", label: "Recently used" },
          { value: "last_used_at", label: "Least recently used" }
        ]}
        onRefresh={() => void keysQuery.refetch()}
      />

      {keysQuery.isLoading ? <LoadingState /> : null}
      {keysQuery.isError ? <ErrorState error={keysQuery.error} /> : null}
      {keysQuery.data?.data.length === 0 ? (
        <EmptyState title="No API keys found" description="Adjust the search or filters." />
      ) : null}
      {keysQuery.data && keysQuery.data.data.length > 0 ? (
        <>
          <div className="rounded-lg border bg-background">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Key</TableHead>
                  <TableHead>User</TableHead>
                  <TableHead>Environment</TableHead>
                  <TableHead>Rate</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keysQuery.data.data.map((apiKey) => {
                  const revoked = Boolean(apiKey.revoked_at);
                  const expired = apiKey.expires_at
                    ? new Date(apiKey.expires_at).getTime() < Date.now()
                    : false;
                  return (
                    <TableRow key={apiKey.id}>
                      <TableCell className="font-medium">{apiKey.name}</TableCell>
                      <TableCell className="font-mono text-xs">
                        {apiKey.prefix}...{apiKey.last_four}
                      </TableCell>
                      <TableCell>
                        <Button asChild variant="link">
                          <Link href={`/admin/users/${apiKey.user_id}`}>Open user</Link>
                        </Button>
                      </TableCell>
                      <TableCell>
                        <Badge variant={apiKey.environment === "live" ? "warning" : "secondary"}>
                          {apiKey.environment}
                        </Badge>
                      </TableCell>
                      <TableCell>{apiKey.rate_limit_per_minute}/min</TableCell>
                      <TableCell>
                        <Badge variant={revoked || expired ? "destructive" : "success"}>
                          {revoked ? "revoked" : expired ? "expired" : "active"}
                        </Badge>
                      </TableCell>
                      <TableCell>{formatDateTime(apiKey.last_used_at)}</TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-2">
                          {canUpdate ? (
                            <Button variant="outline" size="sm" onClick={() => setEditing(apiKey)}>
                              <Edit />
                              Edit
                            </Button>
                          ) : null}
                          {canRevoke ? (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={revoked}
                              onClick={() => setRevoking(apiKey)}
                            >
                              <Trash2 />
                              Revoke
                            </Button>
                          ) : null}
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <AdminPagination
            total={keysQuery.data.total}
            limit={keysQuery.data.limit}
            offset={keysQuery.data.offset}
            onOffsetChange={setOffset}
          />
        </>
      ) : null}

      <Dialog
        open={Boolean(editing)}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit API key</DialogTitle>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={form.handleSubmit((values) => updateMutation.mutate(values))}
          >
            <LabeledControl label="Name">
              <Input {...form.register("name")} />
            </LabeledControl>
            <LabeledControl label="Rate limit per minute">
              <Input type="number" min={1} max={60000} {...form.register("rate_limit_per_minute")} />
            </LabeledControl>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button type="submit" disabled={updateMutation.isPending}>
                Save
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AdminConfirmDialog
        open={Boolean(revoking)}
        onOpenChange={(open) => {
          if (!open) setRevoking(null);
        }}
        title="Revoke this API key?"
        description="The key will stop authenticating requests. Plaintext key material is not available."
        confirmLabel="Revoke API key"
        pending={revokeMutation.isPending}
        onConfirm={() => {
          if (revoking) revokeMutation.mutate(revoking.id);
        }}
      />
    </>
  );
}
