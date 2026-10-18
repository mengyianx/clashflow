import { useEffect, useState } from "react";

type Health = {
  kernel: string;
  state: string;
  respawns: number;
  active_config: string;
};

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

  // Service Control actions: report the resulting Kernel state, then re-poll.
  const runAction = async (action: "start" | "stop" | "restart") => {
    try {
      await fetch(`/api/service/${action}`, { method: "POST" });
      const resp = await fetch("/api/service/health");
      if (resp.ok) setHealth((await resp.json()) as Health);
    } catch {
      setHealth(null);
    }
  };

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
        {health !== null && (
          <>
            <p>
              State:{" "}
              <strong style={{ color: health.state === "degraded" ? "red" : undefined }}>
                {health.state}
              </strong>
              {health.state === "respawning" && ` (respawn ${health.respawns})`}
              {health.state === "degraded" &&
                " — Kernel crash loop gave up; start it again from here"}
            </p>
            <p>
              <button onClick={() => void runAction("start")}>Start</button>{" "}
              <button onClick={() => void runAction("stop")}>Stop</button>{" "}
              <button onClick={() => void runAction("restart")}>Restart</button>
            </p>
          </>
        )}
      </section>
    </main>
  );
}
