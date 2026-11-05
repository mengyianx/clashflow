import { X } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

const VARIANT_CLASS = {
  warn: "border-l-[3px] border-l-warn bg-warn-soft",
  bad: "border-l-[3px] border-l-bad bg-bad-soft",
  info: "border-l-[3px] border-l-line bg-surface-2",
};

export interface AlertProps extends Omit<React.ComponentProps<"div">, "title"> {
  variant?: keyof typeof VARIANT_CLASS;
  title?: React.ReactNode;
  onDismiss?: () => void;
}

/** A page-level banner: warnings stay until dismissed, failures carry the
 * verbatim upstream text underneath a Chinese title. */
export function Alert({ variant = "warn", title, onDismiss, className, children, ...props }: AlertProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-[var(--radius-panel)] px-3.5 py-3 text-[13px] text-ink",
        VARIANT_CLASS[variant],
        className,
      )}
      {...props}
    >
      <div className="min-w-0 flex-1">
        {title ? <div className="font-semibold">{title}</div> : null}
        {children ? <div className="text-ink-2">{children}</div> : null}
      </div>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="关闭提示"
          className="-mt-0.5 -mr-1 rounded p-1 text-ink-3 hover:bg-hover hover:text-ink"
        >
          <X className="size-4" />
        </button>
      ) : null}
    </div>
  );
}

/** The Kernel's own output: monospace, wrapped, never truncated. */
export function Verbatim({ children }: { children: React.ReactNode }) {
  return (
    <pre className="mt-1.5 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-ink-2">
      {children}
    </pre>
  );
}
