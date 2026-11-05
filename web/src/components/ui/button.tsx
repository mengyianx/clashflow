import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 rounded-[var(--radius-control)] border font-medium whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-line bg-surface text-ink hover:bg-hover",
        primary: "border-transparent bg-accent-strong text-on-accent hover:opacity-90",
        ghost: "border-transparent bg-transparent text-ink-2 hover:bg-hover hover:text-ink",
        danger: "border-line bg-surface text-bad hover:bg-bad-soft",
      },
      size: {
        default: "h-9 px-3 text-[13px]",
        sm: "h-8 px-2.5 text-xs",
        icon: "size-8 p-0",
        lg: "h-11 px-4 text-sm",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ComponentProps<"button">,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Component = asChild ? Slot : "button";
  return (
    <Component className={cn(buttonVariants({ variant, size }), className)} {...props} />
  );
}

