"use client";

import { z } from "zod";

import { LoadingState } from "@/components/data-state";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import type { ApiKeyRestrictions } from "@/lib/api";
import type { ApiKeyLimitPeriod, ApiKeyRead } from "@/lib/types";

// HYP-45: the optional per-key conditions (model scoping, IP allowlist, spend
// caps) shared by the create form and the edit dialog. The backend validates
// and normalizes everything again; these checks only catch obvious typos early.

const IP_ENTRY = /^[0-9a-fA-F:.]+(\/\d{1,3})?$/;
const USD_AMOUNT = /^\d+(\.\d{1,2})?$/;

export const limitPeriods: { value: ApiKeyLimitPeriod; label: string; suffix: string }[] = [
  { value: "daily", label: "Daily (resets 00:00 UTC)", suffix: "/ day" },
  { value: "monthly", label: "Monthly (resets on the 1st, UTC)", suffix: "/ month" },
  { value: "never", label: "Never expires (lifetime total)", suffix: "total" }
];

export function splitIpList(value: string): string[] {
  return value
    .split(/[\s,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export const conditionsSchema = z.object({
  allowed_models: z.array(z.string()),
  allowed_ips: z
    .string()
    .refine(
      (value) => splitIpList(value).every((entry) => IP_ENTRY.test(entry)),
      "Enter IP addresses or CIDR ranges, e.g. 203.0.113.5 or 10.0.0.0/8"
    ),
  limit_period: z.enum(["daily", "monthly", "never"]),
  usd_limit: z
    .string()
    .trim()
    .refine(
      (value) => value === "" || (USD_AMOUNT.test(value) && Number(value) > 0),
      "Enter a dollar amount above 0, e.g. 25 or 9.50"
    ),
  credit_limit: z
    .string()
    .trim()
    .refine(
      (value) => value === "" || (/^\d+$/.test(value) && Number(value) > 0),
      "Enter a whole number of credits above 0"
    )
});

export type KeyConditions = z.infer<typeof conditionsSchema>;

export const emptyConditions: KeyConditions = {
  allowed_models: [],
  allowed_ips: "",
  limit_period: "monthly",
  usd_limit: "",
  credit_limit: ""
};

export function conditionsFromKey(key: ApiKeyRead): KeyConditions {
  return {
    allowed_models: key.allowed_models ?? [],
    allowed_ips: (key.allowed_ips ?? []).join("\n"),
    limit_period: key.limit_period ?? "monthly",
    usd_limit: key.usd_limit_cents !== null ? (key.usd_limit_cents / 100).toFixed(2) : "",
    credit_limit: key.credit_limit !== null ? String(key.credit_limit) : ""
  };
}

/** Form values -> API payload. Explicit nulls so an edit can clear a restriction. */
export function conditionsToPayload(values: KeyConditions): Required<ApiKeyRestrictions> {
  const ips = splitIpList(values.allowed_ips);
  const usd = values.usd_limit.trim();
  const credits = values.credit_limit.trim();
  const hasCap = usd !== "" || credits !== "";
  return {
    allowed_models: values.allowed_models.length > 0 ? values.allowed_models : null,
    allowed_ips: ips.length > 0 ? ips : null,
    limit_period: hasCap ? values.limit_period : null,
    usd_limit_cents: usd !== "" ? Math.round(Number(usd) * 100) : null,
    credit_limit: credits !== "" ? Number(credits) : null
  };
}

type FieldErrors = Partial<Record<keyof KeyConditions, string | undefined>>;

export function ApiKeyConditionsFields({
  idPrefix,
  value,
  onChange,
  errors,
  models,
  modelsLoading
}: {
  idPrefix: string;
  value: KeyConditions;
  onChange: (patch: Partial<KeyConditions>) => void;
  errors?: FieldErrors;
  models: string[] | undefined;
  modelsLoading: boolean;
}) {
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Restrict to models (optional)</Label>
        <p className="text-sm text-muted-foreground">
          Leave everything unchecked to let this key reach every model.
        </p>
        {modelsLoading ? <LoadingState /> : null}
        {models && models.length > 0 ? (
          <div className="grid gap-2 sm:grid-cols-3">
            {models.map((modelId) => (
              <label key={modelId} className="flex items-center gap-2 rounded-md border p-3 text-sm">
                <Checkbox
                  checked={value.allowed_models.includes(modelId)}
                  onCheckedChange={(checked) =>
                    onChange({
                      allowed_models: checked
                        ? [...value.allowed_models, modelId]
                        : value.allowed_models.filter((item) => item !== modelId)
                    })
                  }
                />
                <span className="font-mono text-xs">{modelId}</span>
              </label>
            ))}
          </div>
        ) : null}
        {/* A model this key was scoped to that no miner advertises right now. */}
        {value.allowed_models
          .filter((modelId) => !models?.includes(modelId))
          .map((modelId) => (
            <label
              key={modelId}
              className="mr-2 inline-flex items-center gap-2 rounded-md border p-3 text-sm text-muted-foreground"
            >
              <Checkbox
                checked
                onCheckedChange={() =>
                  onChange({ allowed_models: value.allowed_models.filter((item) => item !== modelId) })
                }
              />
              <span className="font-mono text-xs">{modelId}</span>
              <span className="text-xs">(not currently served)</span>
            </label>
          ))}
        {models && models.length === 0 && value.allowed_models.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No models are registered yet. This key will reach every model once one is.
          </p>
        ) : null}
      </div>

      <div className="space-y-2">
        <Label htmlFor={`${idPrefix}-allowed-ips`}>Restrict to IP addresses (optional)</Label>
        <p className="text-sm text-muted-foreground">
          One IP address or CIDR range per line. Leave empty to allow requests from anywhere.
        </p>
        <Textarea
          id={`${idPrefix}-allowed-ips`}
          className="min-h-[88px] font-mono text-xs"
          placeholder={"203.0.113.5\n10.0.0.0/8"}
          value={value.allowed_ips}
          onChange={(event) => onChange({ allowed_ips: event.target.value })}
        />
        {errors?.allowed_ips ? <p className="text-sm text-destructive">{errors.allowed_ips}</p> : null}
      </div>

      <div className="space-y-2">
        <Label>Spend limits (optional)</Label>
        <p className="text-sm text-muted-foreground">
          Requests that would take this key past a limit are rejected until it resets. USD caps
          pay-as-you-go spend; credits cap everything this key uses, including plan and staking
          credits. Leave both empty for no limit.
        </p>
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="space-y-2">
            <Label htmlFor={`${idPrefix}-limit-period`}>Period</Label>
            <NativeSelect
              id={`${idPrefix}-limit-period`}
              value={value.limit_period}
              onChange={(event) =>
                onChange({ limit_period: event.target.value as ApiKeyLimitPeriod })
              }
            >
              {limitPeriods.map((period) => (
                <option key={period.value} value={period.value}>
                  {period.label}
                </option>
              ))}
            </NativeSelect>
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${idPrefix}-usd-limit`}>USD limit</Label>
            <Input
              id={`${idPrefix}-usd-limit`}
              inputMode="decimal"
              placeholder="No limit"
              value={value.usd_limit}
              onChange={(event) => onChange({ usd_limit: event.target.value })}
            />
            {errors?.usd_limit ? <p className="text-sm text-destructive">{errors.usd_limit}</p> : null}
          </div>
          <div className="space-y-2">
            <Label htmlFor={`${idPrefix}-credit-limit`}>Credit limit</Label>
            <Input
              id={`${idPrefix}-credit-limit`}
              inputMode="numeric"
              placeholder="No limit"
              value={value.credit_limit}
              onChange={(event) => onChange({ credit_limit: event.target.value })}
            />
            {errors?.credit_limit ? (
              <p className="text-sm text-destructive">{errors.credit_limit}</p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
