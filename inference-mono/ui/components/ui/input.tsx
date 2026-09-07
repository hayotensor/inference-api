import * as React from "react";

import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          // Fields are transparent over whatever surface holds them, with a
          // Graphite hairline -- the kit draws inputs as cut-outs, not as
          // filled boxes. Focus swaps the border to Off-White on top of the
          // standard ring.
          "flex h-10 w-full rounded-sm border border-input bg-transparent px-3.5 py-2 text-sm transition-colors duration-fast ease-standard file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-stone hover:border-border-strong focus-visible:border-foreground focus-ring disabled:cursor-not-allowed disabled:opacity-40",
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);
Input.displayName = "Input";

export { Input };
