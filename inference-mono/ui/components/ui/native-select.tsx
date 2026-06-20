"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

const NativeSelect = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, disabled, ...props }, ref) => (
  <div className={cn("relative", className)}>
    <select
      ref={ref}
      disabled={disabled}
      className="flex h-10 w-full appearance-none items-center rounded-md border border-input bg-background px-3 py-2 pr-8 text-sm ring-offset-background focus-ring disabled:cursor-not-allowed disabled:opacity-50"
      {...props}
    >
      {children}
    </select>
    <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 opacity-50" />
  </div>
));
NativeSelect.displayName = "NativeSelect";

export { NativeSelect };
