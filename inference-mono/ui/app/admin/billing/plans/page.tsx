"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Edit, Plus, Power } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  AdminConfirmDialog,
  AdminFilterBar,
  AdminPageHeader,
  AdminPagination,
  JsonBlock,
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
import { NativeSelect } from "@/components/ui/native-select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { adminApi, apiErrorMessage } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import type { AdminBillingPlan } from "@/lib/types";

const limit = 25;

const planSchema = z.object({
  slug: z.string().max(80).optional(),
  name: z.string().min(1).max(80),
  description: z.string().max(512).optional(),
  stripe_price_id: z.string().max(255).optional(),
  monthly_token_allowance: z.coerce.number().int().min(0),
  active: z.enum(["true", "false"]),
  visible: z.enum(["true", "false"]),
  sort_order: z.coerce.number().int(),
  features_json: z.string().min(2)
});

type PlanForm = z.infer<typeof planSchema>;

function formValues(plan: AdminBillingPlan | null): PlanForm {
  return {
    slug: plan?.slug ?? "",
    name: plan?.name ?? "",
    description: plan?.description ?? "",
    stripe_price_id: plan?.stripe_price_id ?? "",
    monthly_token_allowance: plan?.monthly_token_allowance ?? 0,
    active: plan?.active === false ? "false" : "true",
    visible: plan?.visible === false ? "false" : "true",
    sort_order: plan?.sort_order ?? 0,
    features_json: JSON.stringify(plan?.features ?? {}, null, 2)
  };
}

function parseFeatures(value: string) {
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("Features must be a JSON object.");
    }
    return parsed;
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : "Invalid JSON.");
  }
}

export default function AdminBillingPlansPage() {
  const admin = useAdmin();
  const queryClient = useQueryClient();
  const [search, setSearch] = React.useState("");
  const [status, setStatus] = React.useState("all");
  const [sort, setSort] = React.useState("sort_order");
  const [offset, setOffset] = React.useState(0);
  const [editing, setEditing] = React.useState<AdminBillingPlan | null>(null);
  const [creating, setCreating] = React.useState(false);
  const [toggling, setToggling] = React.useState<AdminBillingPlan | null>(null);
  const canWrite = hasPermission(admin, "billing.plans.write");

  const plansQuery = useQuery({
    queryKey: ["admin", "billing-plans", { search, status, sort, offset }],
    queryFn: ({ signal }) =>
      adminApi.billingPlans({ limit, offset, search, status, sort }, { signal })
  });

  const activePlan = editing ?? null;
  const form = useForm<PlanForm>({
    resolver: zodResolver(planSchema),
    values: formValues(activePlan)
  });

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["admin", "billing-plans"] });
    await queryClient.invalidateQueries({ queryKey: ["billing-plans"] });
    await queryClient.invalidateQueries({ queryKey: ["admin", "audit-logs"] });
  };

  const createMutation = useMutation({
    mutationFn: (values: PlanForm) =>
      adminApi.createBillingPlan({
        slug: values.slug || null,
        name: values.name,
        description: values.description || null,
        stripe_price_id: values.stripe_price_id || null,
        monthly_token_allowance: values.monthly_token_allowance,
        active: values.active === "true",
        visible: values.visible === "true",
        sort_order: values.sort_order,
        features: parseFeatures(values.features_json)
      }),
    onSuccess: async () => {
      toast.success("Plan created");
      setCreating(false);
      await invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const updateMutation = useMutation({
    mutationFn: (values: PlanForm) =>
      editing
        ? adminApi.updateBillingPlan(editing.id, {
            slug: values.slug || null,
            name: values.name,
            description: values.description || null,
            stripe_price_id: values.stripe_price_id || null,
            monthly_token_allowance: values.monthly_token_allowance,
            active: values.active === "true",
            visible: values.visible === "true",
            sort_order: values.sort_order,
            features: parseFeatures(values.features_json)
          })
        : Promise.reject(new Error("No plan selected")),
    onSuccess: async () => {
      toast.success("Plan updated");
      setEditing(null);
      await invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const activateMutation = useMutation({
    mutationFn: adminApi.activateBillingPlan,
    onSuccess: async () => {
      toast.success("Plan activated");
      setToggling(null);
      await invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const deactivateMutation = useMutation({
    mutationFn: adminApi.deactivateBillingPlan,
    onSuccess: async () => {
      toast.success("Plan deactivated");
      setToggling(null);
      await invalidate();
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const formOpen = creating || Boolean(editing);

  return (
    <>
      <AdminPageHeader
        title="Billing plans"
        description="Manage the plan catalog that the pricing page and checkout flow read from the API."
        action={
          canWrite ? (
            <Button
              onClick={() => {
                setCreating(true);
                setEditing(null);
              }}
            >
              <Plus />
              Create plan
            </Button>
          ) : null
        }
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
          { value: "all", label: "All plans" },
          { value: "active", label: "Active" },
          { value: "inactive", label: "Inactive" },
          { value: "visible", label: "Visible" },
          { value: "hidden", label: "Hidden" }
        ]}
        sort={sort}
        onSortChange={(value) => {
          setSort(value);
          setOffset(0);
        }}
        sortOptions={[
          { value: "sort_order", label: "Sort order" },
          { value: "-sort_order", label: "Reverse sort" },
          { value: "-created_at", label: "Newest first" },
          { value: "created_at", label: "Oldest first" }
        ]}
        onRefresh={() => void plansQuery.refetch()}
      />

      {plansQuery.isLoading ? <LoadingState /> : null}
      {plansQuery.isError ? <ErrorState error={plansQuery.error} /> : null}
      {plansQuery.data?.data.length === 0 ? (
        <EmptyState title="No plans found" description="Create a plan or adjust filters." />
      ) : null}
      {plansQuery.data && plansQuery.data.data.length > 0 ? (
        <>
          <div className="rounded-lg border bg-background">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Plan</TableHead>
                  <TableHead>Allowance</TableHead>
                  <TableHead>Stripe price</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Features</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {plansQuery.data.data.map((plan) => (
                  <TableRow key={plan.id}>
                    <TableCell>
                      <div className="font-medium">{plan.name}</div>
                      <div className="text-xs text-muted-foreground">{plan.slug}</div>
                    </TableCell>
                    <TableCell>{formatNumber(plan.monthly_token_allowance)}</TableCell>
                    <TableCell className="max-w-48 truncate">{plan.stripe_price_id ?? "None"}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        <Badge variant={plan.active ? "success" : "destructive"}>
                          {plan.active ? "active" : "inactive"}
                        </Badge>
                        <Badge variant={plan.visible ? "secondary" : "outline"}>
                          {plan.visible ? "visible" : "hidden"}
                        </Badge>
                      </div>
                    </TableCell>
                    <TableCell>
                      <JsonBlock value={plan.features} />
                    </TableCell>
                    <TableCell>{formatDateTime(plan.updated_at)}</TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-2">
                        {canWrite ? (
                          <>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                setEditing(plan);
                                setCreating(false);
                              }}
                            >
                              <Edit />
                              Edit
                            </Button>
                            <Button
                              variant={plan.active ? "destructive" : "outline"}
                              size="sm"
                              onClick={() => setToggling(plan)}
                            >
                              <Power />
                              {plan.active ? "Deactivate" : "Activate"}
                            </Button>
                          </>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <AdminPagination
            total={plansQuery.data.total}
            limit={plansQuery.data.limit}
            offset={plansQuery.data.offset}
            onOffsetChange={setOffset}
          />
        </>
      ) : null}

      <Dialog
        open={formOpen}
        onOpenChange={(open) => {
          if (!open) {
            setCreating(false);
            setEditing(null);
          }
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editing ? "Edit plan" : "Create plan"}</DialogTitle>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={form.handleSubmit((values) =>
              editing ? updateMutation.mutate(values) : createMutation.mutate(values)
            )}
          >
            <div className="grid gap-3 md:grid-cols-2">
              <LabeledControl label="Name">
                <Input {...form.register("name")} />
              </LabeledControl>
              <LabeledControl label="Slug">
                <Input {...form.register("slug")} />
              </LabeledControl>
              <LabeledControl label="Monthly token allowance">
                <Input type="number" min={0} {...form.register("monthly_token_allowance")} />
              </LabeledControl>
              <LabeledControl label="Stripe price ID">
                <Input {...form.register("stripe_price_id")} />
              </LabeledControl>
              <LabeledControl label="Sort order">
                <Input type="number" {...form.register("sort_order")} />
              </LabeledControl>
              <LabeledControl label="Active">
                <NativeSelect
                  value={form.watch("active")}
                  onChange={(event) =>
                    form.setValue("active", event.target.value as "true" | "false")
                  }
                >
                  <option value="true">Active</option>
                  <option value="false">Inactive</option>
                </NativeSelect>
              </LabeledControl>
              <LabeledControl label="Visible">
                <NativeSelect
                  value={form.watch("visible")}
                  onChange={(event) =>
                    form.setValue("visible", event.target.value as "true" | "false")
                  }
                >
                  <option value="true">Visible</option>
                  <option value="false">Hidden</option>
                </NativeSelect>
              </LabeledControl>
              <LabeledControl label="Description">
                <Input {...form.register("description")} />
              </LabeledControl>
            </div>
            <LabeledControl label="Features JSON">
              <Textarea className="min-h-36 font-mono" {...form.register("features_json")} />
            </LabeledControl>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setCreating(false);
                  setEditing(null);
                }}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={createMutation.isPending || updateMutation.isPending}>
                {editing ? "Save plan" : "Create plan"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AdminConfirmDialog
        open={Boolean(toggling)}
        onOpenChange={(open) => {
          if (!open) setToggling(null);
        }}
        title={toggling?.active ? "Deactivate this plan?" : "Activate this plan?"}
        description={
          toggling?.active
            ? "The plan will be hidden from public pricing and checkout selection."
            : "The plan will become active and visible in admin-managed catalogs."
        }
        confirmLabel={toggling?.active ? "Deactivate plan" : "Activate plan"}
        destructive={Boolean(toggling?.active)}
        pending={activateMutation.isPending || deactivateMutation.isPending}
        onConfirm={() => {
          if (!toggling) return;
          if (toggling.active) deactivateMutation.mutate(toggling.id);
          else activateMutation.mutate(toggling.id);
        }}
      />
    </>
  );
}
