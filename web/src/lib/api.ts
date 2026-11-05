/** Typed client for the Management Panel API.
 *
 * Every panel route answers with FastAPI's `{detail}` on failure; the detail is
 * either a panel message (English, asserted by the Python tests) or the
 * Kernel's verbatim output. `lib/messages.ts` turns it into a Chinese title and
 * keeps the original text for the reader.
 */

export type KernelState = "up" | "down" | "respawning" | "degraded";
export type ConfigKind = "hand-written" | "subscription-derived";
export type SubscriptionState = "ok" | "error" | "pending";
/** The class of the last failed Subscription refresh: `network` (transport
 * failure or non-2xx), `kernel-rejected` (the Kernel's config-test refused
 * the content) or `hot-apply-refused` (the Controller refused the forced
 * hot-apply). Null when there is no class to show. */
export type SubscriptionFailureClass = "network" | "kernel-rejected" | "hot-apply-refused";

export interface ServiceHealth {
  kernel: "up" | "down";
  state: KernelState;
  respawns: number;
  active_config: string;
}

export interface ConfigEntry {
  name: string;
  kind: ConfigKind;
  filename: string;
  active: boolean;
}

export interface ConfigContent extends ConfigEntry {
  content: string;
}

export interface SubscriptionEntry {
  name: string;
  url: string;
  config: string;
  state: SubscriptionState;
  last_refresh_at: string | null;
  last_error: string | null;
  /** The class of the last failed refresh; null for a Subscription that
   * never failed and for a record written before the class existed. */
  last_error_class: SubscriptionFailureClass | null;
  user_agent: string | null;
  update_interval_minutes: number | null;
}

export type UpdateComponent = "kernel" | "metacubexd" | "zashboard";

export interface UpdateState {
  component: UpdateComponent;
  current: string;
  source: "image" | "runtime";
  previous: string | null;
  latest: string | null;
  checked_at: string | null;
  error: string | null;
}

export interface SubscriptionSettings {
  user_agent?: string | null;
  update_interval_minutes?: number | null;
}

/** A failed request: `status` 0 means the panel itself was unreachable. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function readDetail(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) return "";
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const { detail } = parsed;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // Not JSON: the raw body is the message.
  }
  return text;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "panel unreachable");
  }
  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response));
  }
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

function jsonBody(payload: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

const panel = (name: string) => `/api/panel/configs/${encodeURIComponent(name)}`;
const subscription = (name: string) => `/api/panel/subscriptions/${encodeURIComponent(name)}`;

export const api = {
  health: () => request<ServiceHealth>("/api/service/health"),
  login: (password: string) =>
    request<{ authenticated: boolean }>("/api/panel/auth/login", jsonBody({ password })),
  logout: () => request<{ authenticated: boolean }>("/api/panel/auth/logout", { method: "POST" }),

  service: {
    start: () => request<ServiceHealth>("/api/service/start", { method: "POST" }),
    stop: () => request<ServiceHealth>("/api/service/stop", { method: "POST" }),
    restart: () => request<ServiceHealth>("/api/service/restart", { method: "POST" }),
  },

  configs: {
    list: () => request<ConfigEntry[]>("/api/panel/configs"),
    get: (name: string) => request<ConfigContent>(panel(name)),
    create: (name: string, template: boolean) =>
      request<ConfigEntry>("/api/panel/configs", jsonBody({ name, template })),
    copy: (name: string, target: string) =>
      request<ConfigEntry>(`${panel(name)}/copy`, jsonBody({ name: target })),
    // The save endpoint takes the raw YAML body, not JSON.
    save: (name: string, content: string) =>
      request<ConfigEntry>(panel(name), {
        method: "PUT",
        headers: { "Content-Type": "text/plain; charset=utf-8" },
        body: content,
      }),
    activate: (name: string) => request<ConfigEntry>(`${panel(name)}/active`, { method: "PUT" }),
    remove: (name: string) => request<{ deleted: string }>(panel(name), { method: "DELETE" }),
  },

  updates: {
    list: () => request<UpdateState[]>("/api/panel/updates"),
    check: () => request<UpdateState[]>("/api/panel/updates/check", { method: "POST" }),
    update: (component: UpdateComponent) =>
      request<UpdateState[]>(`/api/panel/updates/${component}/update`, { method: "POST" }),
    rollback: (component: UpdateComponent) =>
      request<UpdateState[]>(`/api/panel/updates/${component}/rollback`, { method: "POST" }),
  },

  subscriptions: {
    list: () => request<SubscriptionEntry[]>("/api/panel/subscriptions"),
    add: (name: string, url: string) =>
      request<SubscriptionEntry>("/api/panel/subscriptions", jsonBody({ name, url })),
    refresh: (name: string) =>
      request<SubscriptionEntry>(`${subscription(name)}/refresh`, { method: "POST" }),
    updateSettings: (name: string, settings: SubscriptionSettings) =>
      request<SubscriptionEntry>(subscription(name), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      }),
    remove: (name: string) =>
      request<{ deleted: string }>(subscription(name), { method: "DELETE" }),
  },
};
