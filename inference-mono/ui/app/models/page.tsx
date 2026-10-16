"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  AudioLines,
  Binary,
  Brain,
  Code,
  Eye,
  LayoutGrid,
  MessageSquare,
  Search,
  type LucideIcon
} from "lucide-react";

import { ErrorState, LoadingState } from "@/components/data-state";
import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import { billingApi, modelsApi } from "@/lib/api";
import { formatCompact, formatDateTime, formatMoney, formatMultiplier } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  BillingPlan,
  CatalogModel,
  ModelCategory,
  ModelServiceStatus
} from "@/lib/types";

// HYP-92: public model catalog (see venice.ai/models for the pattern). Data
// comes from the control plane's `GET /models`; category/provider/family/size
// are derived server-side from each model id (api/app/catalog/metadata.py)
// because miners register nothing but an id + hash.

type CategoryFilter = ModelCategory | "all";

const CATEGORIES: { value: ModelCategory; label: string; icon: LucideIcon }[] = [
  { value: "chat", label: "Chat", icon: MessageSquare },
  { value: "reasoning", label: "Reasoning", icon: Brain },
  { value: "code", label: "Code", icon: Code },
  { value: "vision", label: "Vision", icon: Eye },
  { value: "embedding", label: "Embedding", icon: Binary },
  { value: "audio", label: "Audio", icon: AudioLines }
];
const CATEGORY_LABEL = Object.fromEntries(CATEGORIES.map((c) => [c.value, c.label])) as Record<
  ModelCategory,
  string
>;

const STATUS_LABELS: Record<ModelServiceStatus, string> = {
  operational: "Available",
  degraded: "Degraded",
  down: "Offline"
};

/**
 * Cheapest per-credit rate among paid, metered plans -- the basis for the
 * "est. price" column. Credits are baseline tokens, so a model at ×2 costs
 * twice this per token. Null when no such plan is published.
 */
function bestCreditRate(plans: BillingPlan[] | undefined) {
  let best: { centsPerCredit: number; plan: BillingPlan } | null = null;
  for (const plan of plans ?? []) {
    if (!plan.active || plan.is_unlimited || plan.price_cents <= 0 || plan.monthly_token_allowance <= 0) {
      continue;
    }
    const centsPerCredit = plan.price_cents / plan.monthly_token_allowance;
    if (!best || centsPerCredit < best.centsPerCredit) best = { centsPerCredit, plan };
  }
  return best;
}

function matchesSearch(model: CatalogModel, query: string) {
  if (!query) return true;
  return [model.id, model.label, model.provider, model.family, model.parameter_size, CATEGORY_LABEL[model.category]]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(query));
}

export default function ModelsPage() {
  const [category, setCategory] = React.useState<CategoryFilter>("all");
  const [search, setSearch] = React.useState("");

  const catalogQuery = useQuery({
    queryKey: ["model-catalog"],
    queryFn: () => modelsApi.catalog(),
    refetchInterval: 60_000
  });
  // Public plans give the estimated USD price; the table still works without them.
  const plansQuery = useQuery({ queryKey: ["billing-plans"], queryFn: billingApi.plans });
  const creditRate = bestCreditRate(plansQuery.data);

  const models = React.useMemo(() => catalogQuery.data?.models ?? [], [catalogQuery.data]);
  const counts = React.useMemo(() => {
    const result = new Map<ModelCategory, number>();
    for (const model of models) result.set(model.category, (result.get(model.category) ?? 0) + 1);
    return result;
  }, [models]);
  const tabs = CATEGORIES.filter((c) => counts.has(c.value));
  const query = search.trim().toLowerCase();
  const visible = models.filter(
    (model) => (category === "all" || model.category === category) && matchesSearch(model, query)
  );

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <PublicNav />
      <main className="page-shell w-full flex-1 py-12">
        <div className="max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-normal sm:text-4xl">Models</h1>
          <p className="mt-3 text-muted-foreground">
            Every model served on the Talaris network, with its credit rate and live availability.
            Requests draw plan credits per token at the model&apos;s rate.{" "}
            <Link href="/pricing" className="focus-ring text-foreground underline underline-offset-4">
              See plans
            </Link>
            .
          </p>
        </div>

        {catalogQuery.isLoading ? <LoadingState className="mt-8" /> : null}
        {catalogQuery.isError ? (
          <div className="mt-8">
            <ErrorState error={catalogQuery.error} onRetry={() => void catalogQuery.refetch()} />
          </div>
        ) : null}

        {catalogQuery.data ? (
          <>
            <div className="mt-8 flex flex-wrap items-center justify-between gap-3">
              <div
                role="tablist"
                aria-label="Model category"
                className="inline-flex max-w-full flex-wrap items-center gap-0.5 rounded-sm border border-border p-0.5"
              >
                <CategoryTab
                  active={category === "all"}
                  onSelect={() => setCategory("all")}
                  icon={LayoutGrid}
                  label="All"
                  count={models.length}
                />
                {tabs.map((tab) => (
                  <CategoryTab
                    key={tab.value}
                    active={category === tab.value}
                    onSelect={() => setCategory(tab.value)}
                    icon={tab.icon}
                    label={tab.label}
                    count={counts.get(tab.value) ?? 0}
                  />
                ))}
              </div>
              <div className="relative w-full sm:w-72">
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                  strokeWidth={1.5}
                />
                <Input
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search models"
                  aria-label="Search models"
                  className="pl-9"
                />
              </div>
            </div>

            <Card className="mt-4 overflow-hidden">
              <Table id="model-table" aria-label="Models" className="min-w-[60rem]">
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="pl-5">Model</TableHead>
                    <TableHead>Provider</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead className="text-right">Credit rate</TableHead>
                    <TableHead className="text-right">Est. price / 1M tokens</TableHead>
                    <TableHead className="pr-5">Availability</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visible.length === 0 ? (
                    <TableRow className="hover:bg-transparent">
                      <TableCell colSpan={6} className="py-10 text-center text-sm text-muted-foreground">
                        {models.length === 0
                          ? "No models are registered on the network yet."
                          : "No models match these filters."}
                      </TableCell>
                    </TableRow>
                  ) : (
                    visible.map((model) => (
                      <ModelRow
                        key={model.id}
                        model={model}
                        centsPerCredit={creditRate?.centsPerCredit ?? null}
                        currency={creditRate?.plan.currency ?? "usd"}
                      />
                    ))
                  )}
                </TableBody>
              </Table>
            </Card>

            <div className="mt-4 space-y-1.5 text-xs text-muted-foreground">
              <p>
                Credit rate is credits charged per token (×1 = one credit per token).
                {creditRate
                  ? ` Estimated prices use the ${creditRate.plan.name} plan's rate of ${formatMoney(
                      Math.round(creditRate.centsPerCredit * 1_000_000),
                      creditRate.plan.currency
                    )} per million credits.`
                  : null}{" "}
                Provider, category, and size are inferred from each model&apos;s identifier.
              </p>
              <p>
                <Activity aria-hidden="true" className="mr-1.5 inline h-3.5 w-3.5 align-[-2px]" strokeWidth={1.5} />
                Availability as of {formatDateTime(catalogQuery.data.generated_at)}. Refreshes every
                minute. Uptime history is on the{" "}
                <Link href="/status" className="focus-ring text-foreground underline underline-offset-4">
                  status page
                </Link>
                .
              </p>
            </div>
          </>
        ) : null}
      </main>
      <SiteFooter />
    </div>
  );
}

function CategoryTab({
  active,
  onSelect,
  icon: Icon,
  label,
  count
}: {
  active: boolean;
  onSelect: () => void;
  icon: LucideIcon;
  label: string;
  count: number;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      aria-controls="model-table"
      onClick={onSelect}
      className={cn(
        "focus-ring inline-flex items-center gap-2 rounded-sm px-3 py-1.5 text-[11px] font-medium uppercase tracking-label transition-colors duration-fast ease-standard motion-reduce:transition-none",
        active
          ? "bg-primary text-primary-foreground"
          : "text-muted-foreground hover:bg-elevated hover:text-foreground"
      )}
    >
      <Icon aria-hidden="true" className="h-3.5 w-3.5" strokeWidth={1.5} />
      {label}
      <span
        className={cn(
          "font-mono text-[10px] tracking-normal",
          active ? "text-primary-foreground/70" : "text-stone"
        )}
      >
        {count}
      </span>
    </button>
  );
}

function ModelRow({
  model,
  centsPerCredit,
  currency
}: {
  model: CatalogModel;
  centsPerCredit: number | null;
  currency: string;
}) {
  const multiplier = Number(model.token_multiplier);
  const creditsPerMillion = Number.isFinite(multiplier) ? multiplier * 1_000_000 : null;
  const estCents =
    centsPerCredit !== null && creditsPerMillion !== null ? centsPerCredit * creditsPerMillion : null;

  return (
    <TableRow>
      <TableCell className="pl-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="whitespace-nowrap font-mono text-sm text-foreground">{model.name}</span>
          {model.parameter_size ? <Badge variant="outline">{model.parameter_size}</Badge> : null}
        </div>
        {model.name !== model.id ? (
          <div className="mt-0.5 whitespace-nowrap font-mono text-xs text-muted-foreground">{model.id}</div>
        ) : null}
      </TableCell>
      <TableCell>
        <div className="text-sm">{model.provider ?? "—"}</div>
        {model.family && model.family !== model.provider ? (
          <div className="text-xs text-muted-foreground">{model.family} family</div>
        ) : null}
      </TableCell>
      <TableCell>
        <Badge variant="default">{CATEGORY_LABEL[model.category] ?? model.category}</Badge>
      </TableCell>
      <TableCell className="text-right">
        <div className="font-mono text-sm">{formatMultiplier(model.token_multiplier)}</div>
        {creditsPerMillion !== null ? (
          <div className="text-xs text-muted-foreground">
            {formatCompact(creditsPerMillion)} credits / 1M
          </div>
        ) : null}
      </TableCell>
      <TableCell className="text-right font-mono text-sm">
        {estCents === null ? "—" : formatMoney(Math.round(estCents), currency)}
      </TableCell>
      <TableCell className="pr-5">
        <StatusBadge status={model.status}>{STATUS_LABELS[model.status]}</StatusBadge>
        <div className="mt-1 text-xs text-muted-foreground">
          {model.registered_miner_count === 0
            ? "No miners"
            : `${model.healthy_miner_count} of ${model.registered_miner_count} miners`}
        </div>
      </TableCell>
    </TableRow>
  );
}
