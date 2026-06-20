"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Play, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState } from "@/components/data-state";
import { LoadingSpinner } from "@/components/loading-spinner";
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
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import { apiErrorMessage, productApi } from "@/lib/api";
import { formatNumber } from "@/lib/format";

export default function PlaygroundPage() {
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("demo-inference-001");
  const [prompt, setPrompt] = useState("Explain how token allowance is deducted.");
  const [maxTokens, setMaxTokens] = useState(128);

  const enabled = apiKey.trim().length > 0;
  const modelsQuery = useQuery({
    queryKey: ["product-models", apiKey],
    queryFn: ({ signal }) => productApi.models(apiKey.trim(), { signal }),
    enabled,
    retry: false
  });
  const usageQuery = useQuery({
    queryKey: ["product-api-key-usage", apiKey],
    queryFn: ({ signal }) => productApi.usage(apiKey.trim(), { signal }),
    enabled,
    retry: false
  });
  const inferenceMutation = useMutation({
    mutationFn: () =>
      productApi.inference(apiKey.trim(), {
        model,
        prompt,
        max_tokens: maxTokens
      }),
    onSuccess: () => void usageQuery.refetch(),
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const modelIds = useMemo(
    () => modelsQuery.data?.data.map((item) => item.id) ?? ["demo-inference-001"],
    [modelsQuery.data]
  );

  return (
    <>
      <DashboardPageHeader
        title="Playground"
        description="Call the product API with a scoped API key. The key is kept only in component state."
      />

      <div className="grid gap-4 lg:grid-cols-[0.85fr_1.15fr]">
        <Card>
          <CardHeader>
            <CardTitle>Request</CardTitle>
            <CardDescription>Uses `/v1/models`, `/v1/inference`, and `/v1/usage`.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="api-key">API key</Label>
              <Input
                id="api-key"
                type="password"
                autoComplete="off"
                placeholder="sk_test_..."
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-[1fr_140px]">
              <div className="space-y-2">
                <Label>Model</Label>
                <NativeSelect value={model} onChange={(event) => setModel(event.target.value)}>
                  {modelIds.map((modelId) => (
                    <option key={modelId} value={modelId}>
                      {modelId}
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <div className="space-y-2">
                <Label htmlFor="max_tokens">Max tokens</Label>
                <Input
                  id="max_tokens"
                  type="number"
                  min={1}
                  max={4096}
                  value={maxTokens}
                  onChange={(event) => setMaxTokens(Number(event.target.value))}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="prompt">Prompt</Label>
              <Textarea
                id="prompt"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
              />
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <Button
                onClick={() => inferenceMutation.mutate()}
                disabled={!enabled || inferenceMutation.isPending || !prompt.trim()}
              >
                {inferenceMutation.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Play />
                )}
                Run inference
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  void modelsQuery.refetch();
                  void usageQuery.refetch();
                }}
                disabled={!enabled}
              >
                <RefreshCw />
                Refresh metadata
              </Button>
            </div>
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Response</CardTitle>
              <CardDescription>Inference output and usage returned by the API.</CardDescription>
            </CardHeader>
            <CardContent>
              {!inferenceMutation.data ? (
                <EmptyState
                  title="No response yet"
                  description="Paste a key with inference scope and run a prompt."
                />
              ) : (
                <div className="space-y-4">
                  <div className="rounded-lg border bg-muted/35 p-4">
                    <p className="mb-2 text-xs text-muted-foreground">Output</p>
                    <p className="whitespace-pre-wrap text-sm">{inferenceMutation.data.output}</p>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {Object.entries(inferenceMutation.data.usage).map(([label, value]) => (
                      <div key={label} className="rounded-lg border p-3">
                        <p className="text-xs text-muted-foreground">{label}</p>
                        <p className="mt-1 font-semibold">{formatNumber(value)}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>API key usage</CardTitle>
              <CardDescription>Summary from `/v1/usage` for the entered key.</CardDescription>
            </CardHeader>
            <CardContent>
              {!enabled ? (
                <p className="text-sm text-muted-foreground">Enter an API key to load usage.</p>
              ) : usageQuery.isError ? (
                <p className="text-sm text-destructive">{apiErrorMessage(usageQuery.error)}</p>
              ) : usageQuery.data ? (
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Requests</p>
                    <p className="mt-1 font-semibold">{formatNumber(usageQuery.data.requests)}</p>
                  </div>
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Input tokens</p>
                    <p className="mt-1 font-semibold">
                      {formatNumber(usageQuery.data.input_tokens)}
                    </p>
                  </div>
                  <div className="rounded-lg border p-3">
                    <p className="text-xs text-muted-foreground">Output tokens</p>
                    <p className="mt-1 font-semibold">
                      {formatNumber(usageQuery.data.output_tokens)}
                    </p>
                  </div>
                </div>
              ) : (
                <LoadingSpinner className="min-h-16" />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Models</CardTitle>
              <CardDescription>Models returned for the entered API key.</CardDescription>
            </CardHeader>
            <CardContent>
              {modelsQuery.isError ? (
                <p className="text-sm text-destructive">{apiErrorMessage(modelsQuery.error)}</p>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {modelIds.map((modelId) => (
                    <Badge key={modelId} variant="outline">
                      {modelId}
                    </Badge>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </>
  );
}
