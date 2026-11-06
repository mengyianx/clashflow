import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import type { ConfigSummary } from "./api";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** `2026-11-05T03:45:22+00:00` -> `11-05 11:45`（本地时区）。 */
export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** What a Config contains, as of the last validation the panel accepted. */
export function formatSummary(summary: ConfigSummary | null): string {
  if (!summary) return "—";
  const counts = `${summary.nodes} 节点 · ${summary.groups} 组 · ${summary.rules} 规则`;
  return summary.age_key_missing ? `${counts} · 缺 Age key` : counts;
}
