import { useCallback, useEffect, useState } from "react";

type SubscriptionEntry = {
  name: string;
  url: string;
  config: string;
  state: string;
  last_refresh_at: string | null;
  last_error: string | null;
};

// Subscriptions page (ticket #12): list Subscriptions with their state, last
// refresh time and error reason; add a Subscription by URL (the panel fetches
// it into exactly one Subscription-derived Config); refresh manually; delete
// a Subscription together with its Config.
export function SubscriptionsPage() {
  const [subs, setSubs] = useState<SubscriptionEntry[] | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await fetch("/api/panel/subscriptions");
      if (resp.ok) {
        setSubs((await resp.json()) as SubscriptionEntry[]);
        setError(null);
      }
    } catch {
      setSubs(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const reportFailure = async (resp: Response) => {
    let detail = `request failed (${resp.status})`;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // keep the generic message
    }
    setError(detail);
  };

  const add = async () => {
    const resp = await fetch("/api/panel/subscriptions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, url }),
    });
    if (resp.ok) {
      setName("");
      setUrl("");
      await load();
    } else {
      await reportFailure(resp);
    }
  };

  const refresh = async (subName: string) => {
    const resp = await fetch(
      `/api/panel/subscriptions/${encodeURIComponent(subName)}/refresh`,
      { method: "POST" }
    );
    if (resp.ok) await load();
    else await reportFailure(resp);
  };

  const remove = async (subName: string) => {
    const resp = await fetch(`/api/panel/subscriptions/${encodeURIComponent(subName)}`, {
      method: "DELETE",
    });
    if (resp.ok) await load();
    else await reportFailure(resp);
  };

  return (
    <section>
      <h2>Subscriptions</h2>
      {subs === null ? (
        <p>Loading…</p>
      ) : (
        <table style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              <th style={{ paddingRight: 16 }}>Name</th>
              <th style={{ paddingRight: 16 }}>URL</th>
              <th style={{ paddingRight: 16 }}>Config</th>
              <th style={{ paddingRight: 16 }}>State</th>
              <th style={{ paddingRight: 16 }}>Last refresh</th>
              <th>Error</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {subs.map((sub) => (
              <tr key={sub.name}>
                <td style={{ paddingRight: 16 }}>{sub.name}</td>
                <td style={{ paddingRight: 16, maxWidth: 240, overflowWrap: "anywhere" }}>
                  {sub.url}
                </td>
                <td style={{ paddingRight: 16 }}>{sub.config}</td>
                <td style={{ paddingRight: 16 }}>
                  <strong style={{ color: sub.state === "error" ? "red" : undefined }}>
                    {sub.state}
                  </strong>
                </td>
                <td style={{ paddingRight: 16 }}>{sub.last_refresh_at ?? "—"}</td>
                <td style={{ color: "red", maxWidth: 240, overflowWrap: "anywhere" }}>
                  {sub.last_error ?? ""}
                </td>
                <td>
                  <button onClick={() => void refresh(sub.name)}>Refresh</button>{" "}
                  <button onClick={() => void remove(sub.name)}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p>
        <input
          value={name}
          placeholder="new subscription name"
          onChange={(e) => setName(e.target.value)}
        />{" "}
        <input
          value={url}
          placeholder="https://example.com/sub"
          style={{ width: 280 }}
          onChange={(e) => setUrl(e.target.value)}
        />{" "}
        <button disabled={name.trim() === "" || url.trim() === ""} onClick={() => void add()}>
          Add Subscription
        </button>
      </p>
      {error !== null && <p style={{ color: "red" }}>{error}</p>}
    </section>
  );
}
