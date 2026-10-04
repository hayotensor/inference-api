import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Talaris buttons.
 *
 * Per the kit: 2px radius, no shadow, no scale or lift on press. State is
 * carried entirely by fill swap and border color --
 *   primary    Eggshell -> Off-White fill
 *   secondary  transparent -> Charcoal fill, Graphite -> Eggshell border
 *   ghost      transparent -> Charcoal fill
 *   link       Off-White -> Eggshell text
 * `destructive` has no red available to it, so it inverts completely on hover
 * (transparent + Stone Gray border -> solid Off-White on Void Black), which is
 * the loudest the monochrome ramp gets.
 */
const buttonVariants = cva(
  "inline-flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-sm px-4 py-2 text-sm font-medium transition-colors duration-base ease-standard focus-ring disabled:pointer-events-none disabled:opacity-40 [&_svg]:h-4 [&_svg]:w-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary-hover",
        destructive:
          "border border-border-strong bg-transparent text-foreground hover:border-foreground hover:bg-foreground hover:text-background",
        outline:
          "border border-input bg-transparent text-foreground hover:border-eggshell hover:bg-elevated",
        secondary:
          "border border-transparent bg-secondary text-secondary-foreground hover:bg-elevated",
        ghost: "text-muted-foreground hover:bg-elevated hover:text-foreground",
        link: "h-auto px-0 py-0 text-foreground underline-offset-4 hover:text-eggshell hover:underline"
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 px-3 text-[13px]",
        lg: "h-11 px-5",
        icon: "h-10 w-10 px-0"
      }
    },
    defaultVariants: {
      variant: "default",
      size: "default"
    }
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
