/** zashboard keeps backend endpoints in browser storage; its setup route takes
 * them from query params. Point it at the panel's own `/api` reverse proxy (the
 * Kernel secret is injected server-side), on the origin the browser is already
 * using, and hide the two upstream switches v1 rejects: core upgrade (the
 * Kernel version is pinned to the image) and TUN (not part of v1). */
export function zashboardUrl(): string {
  const port =
    window.location.port || (window.location.protocol === "https:" ? "443" : "80");
  const params = new URLSearchParams({
    hostname: window.location.hostname,
    port,
    secondaryPath: "/api",
    disableUpgradeCore: "1",
    disableTunMode: "1",
  });
  return `/zui/#/setup?${params.toString()}`;
}
