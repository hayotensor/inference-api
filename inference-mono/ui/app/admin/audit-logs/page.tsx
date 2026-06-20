"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Eye } from "lucide-react";

import {
  AdminFilterBar,
  AdminPageHeader,
  AdminPagination,
  JsonBlock,
  LabeledControl
} from "@/components/admin-common";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
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
import { adminApi } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AdminAuditLog } from "@/lib/types";

const limit = 25;

export default function AdminAuditLogsPage() {
  const [search, setSearch] = React.useState("");
  const [status, setStatus] = React.useState("all");
  const [sort, setSort] = React.useState("-created_at");
  const [createdFrom, setCreatedFrom] = React.useState("");
  const [createdTo, setCreatedTo] = React.useState("");
  const [offset, setOffset] = React.useState(0);
  const [selected, setSelected] = React.useState<AdminAuditLog | null>(null);

  const auditQuery = useQuery({
    queryKey: ["admin", "audit-logs", { search, status, sort, createdFrom, createdTo, offset }],
    queryFn: ({ signal }) =>
      adminApi.auditLogs({
        limit,
        offset,
        search,
        status: status === "all" ? undefined : status,
        sort,
        created_from: createdFrom ? new Date(createdFrom).toISOString() : undefined,
        created_to: createdTo ? new Date(createdTo).toISOString() : undefined
      }, { signal })
  });

  return (
    <>
      <AdminPageHeader
        title="Audit logs"
        description="Review admin and security-sensitive actions written by the backend."
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
          { value: "all", label: "All results" },
          { value: "success", label: "Success" },
          { value: "failure", label: "Failure" }
        ]}
        sort={sort}
        onSortChange={(value) => {
          setSort(value);
          setOffset(0);
        }}
        sortOptions={[
          { value: "-created_at", label: "Newest first" },
          { value: "created_at", label: "Oldest first" }
        ]}
        onRefresh={() => void auditQuery.refetch()}
      />

      <div className="mb-4 grid gap-3 rounded-lg border bg-background p-3 md:grid-cols-2">
        <LabeledControl label="Created from">
          <Input
            type="datetime-local"
            value={createdFrom}
            onChange={(event) => {
              setCreatedFrom(event.target.value);
              setOffset(0);
            }}
          />
        </LabeledControl>
        <LabeledControl label="Created to">
          <Input
            type="datetime-local"
            value={createdTo}
            onChange={(event) => {
              setCreatedTo(event.target.value);
              setOffset(0);
            }}
          />
        </LabeledControl>
      </div>

      {auditQuery.isLoading ? <LoadingState /> : null}
      {auditQuery.isError ? <ErrorState error={auditQuery.error} /> : null}
      {auditQuery.data?.data.length === 0 ? (
        <EmptyState title="No audit logs found" description="Adjust filters or search terms." />
      ) : null}
      {auditQuery.data && auditQuery.data.data.length > 0 ? (
        <>
          <div className="rounded-lg border bg-background">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Action</TableHead>
                  <TableHead>Actor</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Result</TableHead>
                  <TableHead>Request</TableHead>
                  <TableHead>Time</TableHead>
                  <TableHead className="text-right">Metadata</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {auditQuery.data.data.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="font-medium">{log.action}</TableCell>
                    <TableCell>{log.actor_user_id ?? log.actor ?? "system"}</TableCell>
                    <TableCell>
                      <div>{log.target_type ?? "none"}</div>
                      <div className="max-w-48 truncate text-xs text-muted-foreground">
                        {log.target_id ?? "No target"}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={log.result === "success" ? "success" : "destructive"}>
                        {log.result}
                      </Badge>
                    </TableCell>
                    <TableCell className="max-w-40 truncate">{log.request_id ?? "None"}</TableCell>
                    <TableCell>{formatDateTime(log.created_at)}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="outline" size="sm" onClick={() => setSelected(log)}>
                        <Eye />
                        View
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <AdminPagination
            total={auditQuery.data.total}
            limit={auditQuery.data.limit}
            offset={auditQuery.data.offset}
            onOffsetChange={setOffset}
          />
        </>
      ) : null}

      <Dialog
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      >
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Audit metadata</DialogTitle>
          </DialogHeader>
          {selected ? (
            <div className="space-y-4">
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-md border bg-muted/30 p-3 text-sm">
                  <span className="text-muted-foreground">Action</span>
                  <p className="mt-1 font-medium">{selected.action}</p>
                </div>
                <div className="rounded-md border bg-muted/30 p-3 text-sm">
                  <span className="text-muted-foreground">Created</span>
                  <p className="mt-1 font-medium">{formatDateTime(selected.created_at)}</p>
                </div>
              </div>
              <JsonBlock value={selected.metadata} />
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
