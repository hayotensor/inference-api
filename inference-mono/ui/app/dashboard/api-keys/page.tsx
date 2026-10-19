"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  AlertTriangle,
  BookOpen,
  Clipboard,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  Trash2
} from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { AdminConfirmDialog } from "@/components/admin-common";
import {
  ApiKeyConditionsFields,
  conditionsFromKey,
  conditionsSchema,
  conditionsToPayload,
  emptyConditions,
  limitPeriods,
  splitIpList,
  type KeyConditions
} from "@/components/api-key-conditions";
import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/status-badge";
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
import { apiErrorMessage, apiKeysApi, isVerificationRequiredError } from "@/lib/api";
import { formatDateTime, formatMoney, formatNumber } from "@/lib/format";
import type { ApiKeyCreateResponse, ApiKeyRead, ApiKeyScope } from "@/lib/types";

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
  rate_limit_per_minute: z.coerce.number().int().min(1).max(60000).optional(),
  // HYP-45: optional model / IP / spend conditions. Empty = unrestricted.
  conditions: conditionsSchema
});

type CreateKeyForm = z.infer<typeof createSchema>;

const editSchema = z.object({
  name: z.string().min(1, "Name is required").max(120),
  rate_limit_per_minute: z.coerce.number().int().min(1).max(60000),
  conditions: conditionsSchema
});

type EditKeyForm = z.infer<typeof editSchema>;

function conditionErrors(errors: unknown): Partial<Record<keyof KeyConditions, string>> {
  const fieldErrors = (errors ?? {}) as Partial<Record<keyof KeyConditions, { message?: string }>>;
  return {
    allowed_ips: fieldErrors.allowed_ips?.message,
    usd_limit: fieldErrors.usd_limit?.message,
    credit_limit: fieldErrors.credit_limit?.message
  };
}

function limitSummary(key: ApiKeyRead): string[] {
  const suffix = limitPeriods.find((period) => period.value === key.limit_period)?.suffix ?? "";
  const parts: string[] = [];
  if (key.usd_limit_cents !== null) parts.push(`${formatMoney(key.usd_limit_cents)} ${suffix}`);
  if (key.credit_limit !== null) parts.push(`${formatNumber(key.credit_limit)} credits ${suffix}`);
  return parts;
}

export default function ApiKeysPage() {
  const queryClient = useQueryClient();
  const [plaintextKey, setPlaintextKey] = useState<ApiKeyCreateResponse | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [revoking, setRevoking] = useState<ApiKeyRead | null>(null);
  const [editing, setEditing] = useState<ApiKeyRead | null>(null);
  const form = useForm<CreateKeyForm>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      name: "",
      environment: "test",
      scopes: ["models:read", "inference:write", "usage:read"],
      expires_at: "",
      rate_limit_per_minute: 600,
      conditions: emptyConditions
    }
  });

  const keysQuery = useQuery({
    queryKey: ["api-keys"],
    queryFn: apiKeysApi.list
  });

  const modelsQuery = useQuery({
    queryKey: ["api-keys", "available-models"],
    queryFn: apiKeysApi.availableModels
  });

  const createMutation = useMutation({
    mutationFn: ({ conditions, ...values }: CreateKeyForm) =>
      apiKeysApi.create({
        ...values,
        expires_at: values.expires_at ? new Date(values.expires_at).toISOString() : null,
        rate_limit_per_minute: values.rate_limit_per_minute || null,
        ...conditionsToPayload(conditions)
      }),
    onSuccess: async (response) => {
      setPlaintextKey(response);
      setShowCreate(false);
      form.reset();
      await queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });
  const createError = createMutation.isError ? createMutation.error : null;
  const createNeedsVerification = isVerificationRequiredError(createError);

  const revokeMutation = useMutation({
    mutationFn: apiKeysApi.revoke,
    onSuccess: async () => {
      toast.success("API key revoked");
      setRevoking(null);
      await queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const selectedScopes = form.watch("scopes");
  const createConditions = form.watch("conditions");

  return (
    <>
      <DashboardPageHeader
        title="API keys"
        description="Manage your API keys."
        action={
          <div className="flex items-center gap-2">
            {/* HYP-88: placeholder until the API docs (HYP-34) exist. */}
            <Button variant="outline" onClick={() => toast.info("API docs are coming soon.")}>
              <BookOpen />
              API docs
            </Button>
            <Button
              onClick={() => {
                createMutation.reset();
                setShowCreate((value) => !value);
              }}
            >
              <Plus />
              Create key
            </Button>
          </div>
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

          <div className="mt-4">
            <ApiKeyConditionsFields
              idPrefix="create"
              value={createConditions}
              onChange={(patch) =>
                form.setValue(
                  "conditions",
                  { ...form.getValues("conditions"), ...patch },
                  { shouldValidate: form.formState.isSubmitted }
                )
              }
              errors={conditionErrors(form.formState.errors.conditions)}
              models={modelsQuery.data}
              modelsLoading={modelsQuery.isLoading}
            />
          </div>

          {createError ? (
            <div className="mt-4 flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="space-y-1">
                <p>{apiErrorMessage(createError)}</p>
                {createNeedsVerification ? (
                  <Link href="/dashboard/settings" className="font-medium underline underline-offset-2">
                    Go to Settings to verify your account
                  </Link>
                ) : null}
              </div>
            </div>
          ) : null}

          <div className="mt-5 flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                createMutation.reset();
                setShowCreate(false);
              }}
            >
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
          description="Create a key to call the product API from your own code."
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
                <TableHead>Models</TableHead>
                <TableHead>Restrictions</TableHead>
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
                    <TableCell>
                      {key.allowed_models && key.allowed_models.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {key.allowed_models.map((modelId) => (
                            <Badge key={modelId} variant="outline" className="font-mono text-xs">
                              {modelId}
                            </Badge>
                          ))}
                        </div>
                      ) : (
                        <span className="text-sm text-muted-foreground">All models</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-1 text-sm">
                        {key.allowed_ips && key.allowed_ips.length > 0 ? (
                          <span title={key.allowed_ips.join(", ")}>
                            {key.allowed_ips.length === 1
                              ? <span className="font-mono text-xs">{key.allowed_ips[0]}</span>
                              : `${key.allowed_ips.length} IP ranges`}
                          </span>
                        ) : null}
                        {limitSummary(key).map((part) => (
                          <span key={part}>{part}</span>
                        ))}
                        {!key.allowed_ips?.length && limitSummary(key).length === 0 ? (
                          <span className="text-muted-foreground">None</span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell>{formatDateTime(key.last_used_at)}</TableCell>
                    <TableCell>
                      <StatusBadge status={revoked ? "revoked" : expired ? "expired" : "active"} />
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={revoked}
                          onClick={() => setEditing(key)}
                        >
                          <Pencil />
                          Edit
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={revoked}
                          onClick={() => setRevoking(key)}
                        >
                          <Trash2 />
                          Revoke
                        </Button>
                      </div>
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

      <EditApiKeyDialog
        apiKey={editing}
        models={modelsQuery.data}
        modelsLoading={modelsQuery.isLoading}
        onClose={() => setEditing(null)}
      />

      <AdminConfirmDialog
        open={Boolean(revoking)}
        onOpenChange={(open) => {
          if (!open) setRevoking(null);
        }}
        title="Revoke this API key?"
        description={
          revoking
            ? `"${revoking.name}" will stop authenticating requests immediately. This cannot be undone.`
            : ""
        }
        confirmLabel="Revoke API key"
        pending={revokeMutation.isPending}
        onConfirm={() => {
          if (revoking) revokeMutation.mutate(revoking.id);
        }}
      />
    </>
  );
}

function EditApiKeyDialog({
  apiKey,
  models,
  modelsLoading,
  onClose
}: {
  apiKey: ApiKeyRead | null;
  models: string[] | undefined;
  modelsLoading: boolean;
  onClose: () => void;
}) {
  return (
    <Dialog
      open={Boolean(apiKey)}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit API key</DialogTitle>
          <DialogDescription>
            Changes apply to the next request made with this key. The secret itself does not
            change.
          </DialogDescription>
        </DialogHeader>
        {apiKey ? (
          <EditApiKeyForm
            key={apiKey.id}
            apiKey={apiKey}
            models={models}
            modelsLoading={modelsLoading}
            onClose={onClose}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function EditApiKeyForm({
  apiKey,
  models,
  modelsLoading,
  onClose
}: {
  apiKey: ApiKeyRead;
  models: string[] | undefined;
  modelsLoading: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const form = useForm<EditKeyForm>({
    resolver: zodResolver(editSchema),
    defaultValues: {
      name: apiKey.name,
      rate_limit_per_minute: apiKey.rate_limit_per_minute,
      conditions: conditionsFromKey(apiKey)
    }
  });
  const conditions = form.watch("conditions");
  const hasCaps = apiKey.credit_limit !== null || apiKey.usd_limit_cents !== null;

  const spendQuery = useQuery({
    queryKey: ["api-keys", apiKey.id, "spend"],
    queryFn: ({ signal }) => apiKeysApi.spend(apiKey.id, { signal }),
    enabled: hasCaps
  });

  const updateMutation = useMutation({
    mutationFn: (values: EditKeyForm) =>
      apiKeysApi.update(apiKey.id, {
        name: values.name,
        rate_limit_per_minute: values.rate_limit_per_minute,
        ...conditionsToPayload(values.conditions)
      }),
    onSuccess: async () => {
      toast.success("API key updated");
      onClose();
      await queryClient.invalidateQueries({ queryKey: ["api-keys"] });
    }
  });

  const ipCount = splitIpList(conditions.allowed_ips).length;
  const period = limitPeriods.find((item) => item.value === spendQuery.data?.limit_period);

  return (
    <form
      className="space-y-4"
      onSubmit={form.handleSubmit((values) => updateMutation.mutate(values))}
    >
      <div className="grid gap-4 sm:grid-cols-[1fr_180px]">
        <div className="space-y-2">
          <Label htmlFor="edit-name">Key name</Label>
          <Input id="edit-name" {...form.register("name")} />
          {form.formState.errors.name ? (
            <p className="text-sm text-destructive">{form.formState.errors.name.message}</p>
          ) : null}
        </div>
        <div className="space-y-2">
          <Label htmlFor="edit-rate-limit">Rate limit/min</Label>
          <Input
            id="edit-rate-limit"
            type="number"
            min={1}
            max={60000}
            {...form.register("rate_limit_per_minute")}
          />
        </div>
      </div>

      <div className="rounded-md border bg-muted p-3 font-mono text-xs">
        {apiKey.prefix}...{apiKey.last_four}
      </div>

      {hasCaps && spendQuery.data ? (
        <div className="rounded-md border p-3 text-sm">
          <p className="font-medium">
            Used {period?.value === "never" ? "in total" : period?.value === "daily" ? "today" : "this month"}
          </p>
          <p className="text-muted-foreground">
            {apiKey.usd_limit_cents !== null
              ? `${formatMoney(spendQuery.data.usd_cents_used)} of ${formatMoney(apiKey.usd_limit_cents)}`
              : null}
            {apiKey.usd_limit_cents !== null && apiKey.credit_limit !== null ? " · " : null}
            {apiKey.credit_limit !== null
              ? `${formatNumber(spendQuery.data.credits_used)} of ${formatNumber(apiKey.credit_limit)} credits`
              : null}
            {spendQuery.data.resets_at ? ` · resets ${formatDateTime(spendQuery.data.resets_at)}` : null}
          </p>
        </div>
      ) : null}

      <ApiKeyConditionsFields
        idPrefix="edit"
        value={conditions}
        onChange={(patch) =>
          form.setValue(
            "conditions",
            { ...form.getValues("conditions"), ...patch },
            { shouldValidate: form.formState.isSubmitted, shouldDirty: true }
          )
        }
        errors={conditionErrors(form.formState.errors.conditions)}
        models={models}
        modelsLoading={modelsLoading}
      />

      {ipCount > 0 ? (
        <p className="text-sm text-muted-foreground">
          Requests from any other address will be rejected with 403 ip_not_allowed. Make sure your
          current server address is included before saving.
        </p>
      ) : null}

      {updateMutation.isError ? (
        <div className="flex items-start gap-3 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <p>{apiErrorMessage(updateMutation.error)}</p>
        </div>
      ) : null}

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={updateMutation.isPending}>
          {updateMutation.isPending ? <Loader2 className="animate-spin" /> : null}
          Save changes
        </Button>
      </DialogFooter>
    </form>
  );
}
