# Runtime API access via backend reverse proxy

The mihomo Controller binds 127.0.0.1 only (pinned with `-ext-ctl`/`-secret`, which unconditionally override YAML and survive SIGHUP), and ClashFlow's backend reverse-proxies `/api/*` — WebSockets included — to it, injecting the secret server-side. The secret never reaches the browser, the Controller (an API that can rewrite every Config) stays off the LAN, and the panel password is the single auth surface. The panel itself being plain HTTP on the LAN is an accepted v1 trade-off for a trusted home network; the rejected option differs in kind, not degree — a long-lived, full-power secret cached in every browser that connects, versus a session-scoped login sniffable only on the LAN.

## Considered Options

- **Controller on the LAN with a secret; dashboards connect directly** (the OpenClash / metacubexd default) — rejected: metacubexd stores the secret in plaintext `localStorage`, and anything on the LAN could reach an API with full config control.
- **Direct exposure behind an opt-in switch** — not in v1; can be added later for third-party dashboards without revisiting this default.
