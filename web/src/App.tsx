import { useEffect, useState } from "react";

type Health = { kernel: string; active_config: string };

// Navigation shell: later tickets add real pages (Configs, Subscriptions,
// Editor) here as routes, keeping this file's structure.
const NAV_ITEMS = ["Overview", "Configs", "Subscriptions"];

export function App() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const resp = await fetch("/api/service/health");
        if (resp.ok) {
          const body = (await resp.json()) as Health;
          if (!cancelled) setHealth(body);
        }
      } catch {
        if (!cancelled) setHealth(null);
      }
    };
    void poll();
    const timer = setInterval(() => void poll(), 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", margin: "2rem auto", maxWidth: 480 }}>
      <h1>ClashFlow</h1>
      <nav>
        {NAV_ITEMS.map((item) => (
          <span key={item} style={{ marginRight: 12 }}>
            {item}
          </span>
        ))}
      </nav>
      <section>
        <h2>Overview</h2>
        {health === null ? (
          <p>Loading…</p>
        ) : (
          <p>
            Kernel: <strong>{health.kernel}</strong> · Active Config:{" "}
            <strong>{health.active_config}</strong>
          </p>
        )}
      </section>
    </main>
  );
}
