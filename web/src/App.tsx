import { useCallback, useEffect, useState } from "react";
import { ConfigsPage } from "./ConfigsPage";
import { EditorPage } from "./EditorPage";
import { LoginPage } from "./LoginPage";
import { SubscriptionsPage } from "./SubscriptionsPage";

type Health = {
  kernel: string;
  state: string;
  respawns: number;
  active_config: string;
};

// Navigation shell: later tickets add real pages (Subscriptions) here as
// routes, keeping this file's structure. The Editor opens from the Configs
// page ("Edit") for a picked Config.
const NAV_ITEMS = ["Overview", "Configs", "Subscriptions"];

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  // #17: session gate — null until the first health probe settles; a 401
  // (never logged in, or the session ended) shows the LoginPage.
  const [authed, setAuthed] = useState<boolean | null>(null);

  const poll = useCallback(async () => {
    try {
      const resp = await fetch("/api/service/health");
      if (resp.status === 401) {
        setHealth(null);
        setAuthed(false);
        return;
      }
      if (resp.ok) {
        const body = (await resp.json()) as Health;
        setHealth(body);
        setAuthed(true);
      }
    } catch {
      setHealth(null);
    }
  }, []);

  useEffect(() => {
    void poll();
    const timer = setInterval(() => void poll(), 5000);
    return () => clearInterval(timer);
  }, [poll]);

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

  // #17: logout revokes the session server-side, then the shell falls back to
  // the LoginPage on the next poll.
  const logout = async () => {
    await fetch("/api/panel/auth/logout", { method: "POST" });
    setEditing(null);
    await poll();
  };

  if (authed === false) {
    return <LoginPage onLogin={() => void poll()} />;
  }

  return (
    <main style={{ fontFamily: "system-ui", margin: "2rem auto", maxWidth: 480 }}>
      <h1>ClashFlow</h1>
      <nav>
        {NAV_ITEMS.map((item) => (
          <span key={item} style={{ marginRight: 12 }}>
            {item}
          </span>
        ))}
        <button onClick={() => void logout()}>Logout</button>
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
        {/* Runtime Dashboard entry (plan §4): the embedded metacubexd bundle. */}
        <p>
          <a href="/ui/">Open the Runtime Dashboard</a> — nodes, delay tests, connections, traffic,
          logs
        </p>
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
      <ConfigsPage onEdit={setEditing} />
      <SubscriptionsPage />
      {editing !== null && <EditorPage key={editing} configName={editing} onOpen={setEditing} />}
    </main>
  );
}
