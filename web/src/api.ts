// Shared panel-API error surface: a failed response's `detail` (FastAPI
// errors, Kernel-verbatim where applicable) replaces the generic
// status-based message. One helper for every page's fetch call sites.
export async function reportFailure(resp: Response): Promise<string> {
  let detail = `request failed (${resp.status})`;
  try {
    const body = (await resp.json()) as { detail?: string };
    if (body.detail) detail = body.detail;
  } catch {
    // keep the generic message
  }
  return detail;
}
