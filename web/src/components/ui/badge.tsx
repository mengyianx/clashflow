import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs whitespace-nowrap",
  {
    variants: {
      variant: {
        ok: "bg-ok-soft text-ok",
        bad: "bg-bad-soft text-bad",
        warn: "bg-warn-soft text-warn",
        mute: "bg-surface-2 text-ink-3",
      },
      dot: { true: "", false: "" },
    },
    defaultVariants: { variant: "mute", dot: false },
  },
);

export interface BadgeProps
  extends React.ComponentProps<"span">,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, dot, children, ...props }: BadgeProps) {
  const classes = badgeVariants({ variant, dot });
  return (
    <span className={cn(classes, className)} {...props}>
      {dot ? <span className="size-1.5 rounded-full bg-current" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}
