import { useCallback, useEffect, useState } from "react";

type ConfigEntry = {
  name: string;
  kind: string;
  filename: string;
  active: boolean;
};

// Configs page (ticket #9): list registered Configs with the Active one
// marked, create empty Configs, delete them, and choose the Active Config.
// Choosing takes effect on the next Kernel (re)start; the validate +
// hot-apply pipeline arrives with ticket #10.
export function ConfigsPage() {
  const [configs, setConfigs] = useState<ConfigEntry[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const resp = await fetch("/api/panel/configs");
      if (resp.ok) {
        setConfigs((await resp.json()) as ConfigEntry[]);
        setError(null);
      }
    } catch {
      setConfigs(null);
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

  const create = async () => {
    const resp = await fetch("/api/panel/configs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (resp.ok) {
      setName("");
      await load();
    } else {
      await reportFailure(resp);
    }
  };

  const setActive = async (configName: string) => {
    const resp = await fetch(`/api/panel/configs/${encodeURIComponent(configName)}/active`, {
      method: "PUT",
    });
    if (resp.ok) await load();
    else await reportFailure(resp);
  };

  const remove = async (configName: string) => {
    const resp = await fetch(`/api/panel/configs/${encodeURIComponent(configName)}`, {
      method: "DELETE",
    });
    if (resp.ok) await load();
    else await reportFailure(resp);
  };

  return (
    <section>
      <h2>Configs</h2>
      {configs === null ? (
        <p>Loading…</p>
      ) : (
        <table style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left" }}>
              <th style={{ paddingRight: 16 }}>Name</th>
              <th style={{ paddingRight: 16 }}>Kind</th>
              <th style={{ paddingRight: 16 }}>Filename</th>
              <th style={{ paddingRight: 16 }}>Active</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {configs.map((config) => (
              <tr key={config.name}>
                <td style={{ paddingRight: 16 }}>{config.name}</td>
                <td style={{ paddingRight: 16 }}>{config.kind}</td>
                <td style={{ paddingRight: 16 }}>{config.filename}</td>
                <td style={{ paddingRight: 16 }}>{config.active ? "●" : ""}</td>
                <td>
                  {!config.active && (
                    <>
                      <button onClick={() => void setActive(config.name)}>Set active</button>{" "}
                    </>
                  )}
                  <button disabled={config.active} onClick={() => void remove(config.name)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p>
        <input
          value={name}
          placeholder="new config name"
          onChange={(e) => setName(e.target.value)}
        />{" "}
        <button disabled={name.trim() === ""} onClick={() => void create()}>
          Create empty Config
        </button>
      </p>
      {error !== null && <p style={{ color: "red" }}>{error}</p>}
    </section>
  );
}
