"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Clipboard, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import { apiErrorMessage, apiKeysApi } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { ApiKeyCreateResponse, ApiKeyScope } from "@/lib/types";

const scopes: { value: ApiKeyScope; label: string }[] = [
  { value: "models:read", label: "Read models" },
  { value: "inference:write", label: "Run inference" },
  { value: "usage:read", label: "Read usage" }
];

const createSchema = z.object({
  name: z.string().min(1, "Name is required").max(120),
  environment: z.enum(["test", "live"]),
  scopes: z.array(z.enum(["models:read", "inference:write", "usage:read"])).min(1),
  expires_at: z.string().optional(),
  rate_limit_per_minute: z.coerce.number().int().min(1).max(60000).optional()
});

type CreateKeyForm = z.infer<typeof createSchema>;

export default function ApiKeysPage() {
  const queryClient = useQueryClient();
  const [plaintextKey, setPlaintextKey] = useState<ApiKeyCreateResponse | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const form = useForm<CreateKeyForm>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      name: "",
      environment: "test",
      scopes: ["models:read", "inference:write", "usage:read"],
      expires_at: "",
      rate_limit_per_minute: 600
    }
  });

  const keysQuery = useQuery({
    queryKey: ["api-keys"],
    queryFn: apiKeysApi.list
  });

  const createMutation = useMutation({
    mutationFn: (values: CreateKeyForm) =>
      apiKeysApi.create({
        ...values,
        expires_at: values.expires_at ? new Date(values.expires_at).toISOString() : null,
        rate_limit_per_minute: values.rate_limit_per_minute || null
      }),
    onSuccess: async (response) => {
      setPlaintextKey(response);
      setShowCreate(false);
      form.reset();
      await queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const revokeMutation = useMutation({
    mutationFn: apiKeysApi.revoke,
    onSuccess: async () => {
      toast.success("API key revoked");
      await queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const selectedScopes = form.watch("scopes");

  return (
    <>
      <DashboardPageHeader
        title="API keys"
        description="Create scoped credentials and revoke them through the backend API."
        action={
          <Button onClick={() => setShowCreate((value) => !value)}>
            <Plus />
            Create key
          </Button>
        }
      />

      {showCreate ? (
        <form
          className="mb-6 rounded-lg border bg-background p-5"
          onSubmit={form.handleSubmit((values) => createMutation.mutate(values))}
        >
          <div className="grid gap-4 lg:grid-cols-[1fr_180px_180px]">
            <div className="space-y-2">
              <Label htmlFor="name">Key name</Label>
              <Input id="name" placeholder="Production app" {...form.register("name")} />
              {form.formState.errors.name ? (
                <p className="text-sm text-destructive">{form.formState.errors.name.message}</p>
              ) : null}
            </div>
            <div className="space-y-2">
              <Label>Environment</Label>
              <NativeSelect
                value={form.watch("environment")}
                onChange={(event) =>
                  form.setValue("environment", event.target.value as "test" | "live")
                }
              >
                <option value="test">Test</option>
                <option value="live">Live</option>
              </NativeSelect>
            </div>
            <div className="space-y-2">
              <Label htmlFor="rate_limit">Rate limit/min</Label>
              <Input
                id="rate_limit"
                type="number"
                min={1}
                max={60000}
                {...form.register("rate_limit_per_minute")}
              />
            </div>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_260px]">
            <div className="space-y-2">
              <Label>Scopes</Label>
              <div className="grid gap-2 sm:grid-cols-3">
                {scopes.map((scope) => (
                  <label
                    key={scope.value}
                    className="flex items-center gap-2 rounded-md border p-3 text-sm"
                  >
                    <Checkbox
                      checked={selectedScopes.includes(scope.value)}
                      onCheckedChange={(checked) => {
                        const next = checked
                          ? [...selectedScopes, scope.value]
                          : selectedScopes.filter((value) => value !== scope.value);
                        form.setValue("scopes", next, { shouldValidate: true });
                      }}
                    />
                    {scope.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="expires_at">Expiration</Label>
              <Input id="expires_at" type="datetime-local" {...form.register("expires_at")} />
            </div>
          </div>

          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setShowCreate(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? <Loader2 className="animate-spin" /> : <KeyRound />}
              Create API key
            </Button>
          </div>
        </form>
      ) : null}

      {keysQuery.isLoading ? <LoadingState /> : null}
      {keysQuery.isError ? (
        <ErrorState error={keysQuery.error} onRetry={() => void keysQuery.refetch()} />
      ) : null}
      {keysQuery.data?.length === 0 ? (
        <EmptyState
          title="No API keys"
          description="Create a key to call the product API from your own code or the playground."
          action={
            <Button onClick={() => setShowCreate(true)}>
              <Plus />
              Create key
            </Button>
          }
        />
      ) : null}
      {keysQuery.data && keysQuery.data.length > 0 ? (
        <div className="rounded-lg border bg-background">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Key</TableHead>
                <TableHead>Environment</TableHead>
                <TableHead>Scopes</TableHead>
                <TableHead>Last used</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {keysQuery.data.map((key) => {
                const revoked = Boolean(key.revoked_at);
                const expired = key.expires_at
                  ? new Date(key.expires_at).getTime() < Date.now()
                  : false;
                return (
                  <TableRow key={key.id}>
                    <TableCell className="font-medium">{key.name}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {key.prefix}...{key.last_four}
                    </TableCell>
                    <TableCell>
                      <Badge variant={key.environment === "live" ? "warning" : "secondary"}>
                        {key.environment}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {key.scopes.map((scope) => (
                          <Badge key={scope} variant="outline">
                            {scope}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>{formatDateTime(key.last_used_at)}</TableCell>
                    <TableCell>
                      <Badge variant={revoked || expired ? "destructive" : "success"}>
                        {revoked ? "revoked" : expired ? "expired" : "active"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={revoked || revokeMutation.isPending}
                        onClick={() => revokeMutation.mutate(key.id)}
                      >
                        <Trash2 />
                        Revoke
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      ) : null}

      <Dialog
        open={Boolean(plaintextKey)}
        onOpenChange={(open) => {
          if (!open) setPlaintextKey(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Copy your API key</DialogTitle>
            <DialogDescription>
              The plaintext key is returned once by the backend. It will not be stored or shown
              again after this dialog closes.
            </DialogDescription>
          </DialogHeader>
          {plaintextKey ? (
            <div className="space-y-4">
              <div className="rounded-md border bg-muted p-3 font-mono text-sm break-all">
                {plaintextKey.key}
              </div>
              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => {
                    void navigator.clipboard.writeText(plaintextKey.key);
                    toast.success("Copied API key");
                  }}
                >
                  <Clipboard />
                  Copy
                </Button>
                <Button onClick={() => setPlaintextKey(null)}>Done</Button>
              </DialogFooter>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
