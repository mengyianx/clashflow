import * as React from "react";

import { cn } from "@/lib/utils";

export function Input({ className, type = "text", ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      className={cn(
        "h-8 w-full rounded-[var(--radius-control)] border border-line bg-surface px-2.5 text-[13px] text-ink",
        "placeholder:text-ink-3 focus:border-accent focus:ring-4 focus:ring-[var(--tint)] focus:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}
