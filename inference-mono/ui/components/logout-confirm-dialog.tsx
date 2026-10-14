"use client";

import * as React from "react";
import { LogOut } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from "@/components/ui/dialog";

/**
 * Confirms a user-initiated "log out" click before it fires (HYP-46). Shared
 * by `PublicNav`, `DashboardShell`, and `AdminShell` -- built directly on the
 * `components/ui/dialog.tsx` primitive (rather than importing
 * `AdminConfirmDialog`, which lives in `admin-common.tsx` alongside
 * admin-only helpers) so non-admin surfaces don't pull in admin-shell's
 * dependency graph. Mirrors `AdminConfirmDialog`'s layout/visual pattern for
 * consistency.
 *
 * Escape-to-close and outside-click-to-close come for free from Radix's
 * Dialog. Cancel is forced to hold focus on open (rather than trusting
 * Radix's default, which focuses the dialog content itself) so the safe,
 * non-destructive action is what Enter/Space hits if the user doesn't
 * deliberately reach for Log out.
 */
export function LogoutConfirmDialog({
  open,
  onOpenChange,
  onConfirm,
  onCloseAutoFocus
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  onCloseAutoFocus?: React.ComponentPropsWithoutRef<typeof DialogContent>["onCloseAutoFocus"];
}) {
  const cancelRef = React.useRef<HTMLButtonElement>(null);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        onCloseAutoFocus={onCloseAutoFocus}
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          cancelRef.current?.focus();
        }}
      >
        <DialogHeader>
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-sm border border-border-strong text-foreground">
            <LogOut className="h-5 w-5" strokeWidth={1.5} />
          </div>
          <DialogTitle>Log out?</DialogTitle>
          <DialogDescription>
            You&apos;ll need to sign in again to access your account.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button ref={cancelRef} variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            <LogOut strokeWidth={1.5} />
            Log out
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
