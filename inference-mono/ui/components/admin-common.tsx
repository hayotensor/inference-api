"use client";

import * as React from "react";
import { AlertTriangle, Loader2, RefreshCw, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
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
import { cn } from "@/lib/utils";
import type { AdminPermission } from "@/lib/types";
import { useAdmin } from "@/components/admin-shell";

export function AdminPageHeader({
  title,
  description,
  action
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-normal">{title}</h1>
        {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function PermissionGate({
  permission,
  children,
  fallback = null
}: {
  permission: AdminPermission;
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const admin = useAdmin();
  return admin.permissions.includes(permission) ? <>{children}</> : <>{fallback}</>;
}

export function hasPermission(admin: { permissions: AdminPermission[] }, permission: AdminPermission) {
  return admin.permissions.includes(permission);
}

export function AdminFilterBar({
  search,
  onSearchChange,
  status,
  onStatusChange,
  statusOptions,
  sort,
  onSortChange,
  sortOptions,
  onRefresh
}: {
  search: string;
  onSearchChange: (value: string) => void;
  status?: string;
  onStatusChange?: (value: string) => void;
  statusOptions?: { value: string; label: string }[];
  sort?: string;
  onSortChange?: (value: string) => void;
  sortOptions?: { value: string; label: string }[];
  onRefresh?: () => void;
}) {
  return (
    <div className="mb-4 grid gap-3 rounded-lg border bg-background p-3 lg:grid-cols-[1fr_auto_auto_auto]">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search"
          className="pl-9"
        />
      </div>
      {statusOptions && status !== undefined && onStatusChange ? (
        <NativeSelect
          className="min-w-40"
          value={status}
          onChange={(event) => onStatusChange(event.target.value)}
        >
          {statusOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
      ) : null}
      {sortOptions && sort !== undefined && onSortChange ? (
        <NativeSelect
          className="min-w-44"
          value={sort}
          onChange={(event) => onSortChange(event.target.value)}
        >
          {sortOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </NativeSelect>
      ) : null}
      {onRefresh ? (
        <Button variant="outline" onClick={onRefresh}>
          <RefreshCw />
          Refresh
        </Button>
      ) : null}
    </div>
  );
}

export function AdminPagination({
  total,
  limit,
  offset,
  onOffsetChange
}: {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (value: number) => void;
}) {
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(total, offset + limit);
  return (
    <div className="mt-4 flex flex-col gap-3 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
      <span>
        Showing {start}-{end} of {total}
      </span>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset <= 0}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + limit >= total}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

export function AdminConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  pending = false,
  destructive = true,
  onOpenChange,
  onConfirm
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  pending?: boolean;
  destructive?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-md bg-amber-100 text-amber-700">
            <AlertTriangle className="h-5 w-5" />
          </div>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            onClick={onConfirm}
            disabled={pending}
          >
            {pending ? <Loader2 className="animate-spin" /> : null}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function FieldGrid({
  children,
  className
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={cn("grid gap-4 md:grid-cols-2 xl:grid-cols-3", className)}>{children}</div>;
}

export function ReadOnlyField({
  label,
  value
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="rounded-md border bg-muted/30 p-3">
      <p className="text-xs font-medium uppercase tracking-normal text-muted-foreground">{label}</p>
      <div className="mt-1 min-h-5 break-words text-sm font-medium">{value || "Not set"}</div>
    </div>
  );
}

export function LabeledControl({
  label,
  children
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-80 overflow-auto rounded-md border bg-muted p-3 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}
