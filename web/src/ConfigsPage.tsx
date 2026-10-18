import { useCallback, useEffect, useState } from "react";

type ConfigEntry = {
  name: string;
  kind: string;
  filename: string;
  active: boolean;
};

// Configs page (ticket #9): list registered Configs with the Active one
// marked, create empty Configs, delete them, and choose the Active Config.
// Switching now validates + hot-applies through the save pipeline (ticket
// #10); "Edit" opens the raw-YAML Editor for a Config. Ticket #11 adds
// "New from template" (blank skeleton, immediately runnable) and "Copy to
// Hand-written" (any Config duplicated into an independent hand-written
// Config refreshes never touch).
export function ConfigsPage({ onEdit }: { onEdit?: (name: string) => void }) {
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

  const create = async (template: boolean) => {
    const resp = await fetch("/api/panel/configs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(template ? { name, template: true } : { name }),
    });
    if (resp.ok) {
      setName("");
      await load();
    } else {
      await reportFailure(resp);
    }
  };

  const copyToHandwritten = async (configName: string) => {
    const target = window.prompt(
      `Copy "${configName}" to a new Hand-written Config:`,
      `${configName}-copy`,
    );
    if (target === null || target.trim() === "") return;
    const resp = await fetch(`/api/panel/configs/${encodeURIComponent(configName)}/copy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: target }),
    });
    if (resp.ok) await load();
    else await reportFailure(resp);
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
                  <button onClick={() => onEdit?.(config.name)}>Edit</button>{" "}
                  <button onClick={() => void copyToHandwritten(config.name)}>
                    Copy to Hand-written
                  </button>{" "}
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
        <button disabled={name.trim() === ""} onClick={() => void create(false)}>
          Create empty Config
        </button>{" "}
        <button disabled={name.trim() === ""} onClick={() => void create(true)}>
          New from template
        </button>
      </p>
      {error !== null && <p style={{ color: "red" }}>{error}</p>}
    </section>
  );
}
