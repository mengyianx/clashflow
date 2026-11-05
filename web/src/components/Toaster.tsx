import { Toaster as SonnerToaster } from "sonner";

import type { Theme } from "@/hooks/useTheme";

/** Success feedback only; failures use the persistent page banner. */
export function Toaster({ theme }: { theme: Theme }) {
  return (
    <SonnerToaster
      theme={theme}
      position="bottom-right"
      offset={24}
      toastOptions={{
        style: {
          background: "var(--ink)",
          color: "var(--canvas)",
          border: "1px solid var(--line-soft)",
          borderRadius: "var(--radius-panel)",
          fontSize: "13px",
        },
      }}
    />
  );
}
