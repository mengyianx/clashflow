import { useCallback, useEffect, useState } from "react";

type ConfigContent = {
  name: string;
  kind: string;
  filename: string;
  active: boolean;
  content: string;
};

// Editor page (ticket #10): pick a Config on the Configs page ("Edit"), edit
// its raw YAML, and save it through the validate + atomic-swap pipeline. A
// failed save shows the Kernel's error output verbatim; a successful save to
// the Active Config hot-applies to the running Kernel without a restart.
export function EditorPage({ configName }: { configName: string }) {
  const [content, setContent] = useState<string | null>(null);
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const reportFailure = async (resp: Response) => {
    let detail = `request failed (${resp.status})`;
    try {
      const body = (await resp.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // keep the generic message
    }
    setError(detail);
    setSaved(false);
  };

  const load = useCallback(async () => {
    try {
      const resp = await fetch(`/api/panel/configs/${encodeURIComponent(configName)}`);
      if (resp.ok) {
        const body = (await resp.json()) as ConfigContent;
        setContent(body.content);
        setActive(body.active);
        setError(null);
        setSaved(false);
      } else {
        await reportFailure(resp);
      }
    } catch {
      setError("panel unreachable");
    }
  }, [configName]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (content === null) return;
    try {
      const resp = await fetch(`/api/panel/configs/${encodeURIComponent(configName)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/yaml" },
        body: content,
      });
      if (resp.ok) {
        const body = (await resp.json()) as ConfigContent;
        setActive(body.active);
        setError(null);
        setSaved(true);
      } else {
        await reportFailure(resp);
      }
    } catch {
      setError("panel unreachable");
      setSaved(false);
    }
  };

  return (
    <section>
      <h2>Editor</h2>
      <p>
        Editing <strong>{configName}</strong>
        {active ? " (Active Config — saving hot-applies to the running Kernel)" : ""}
      </p>
      {content === null ? (
        <p>Loading…</p>
      ) : (
        <>
          <textarea
            style={{ width: "100%", minHeight: 240, fontFamily: "monospace" }}
            value={content}
            spellCheck={false}
            onChange={(event) => {
              setContent(event.target.value);
              setSaved(false);
            }}
          />
          <p>
            <button onClick={() => void save()}>Save</button>{" "}
            <button onClick={() => void load()}>Reload</button>
            {saved ? <span> — saved{active ? ", hot-applied" : ""}</span> : null}
          </p>
        </>
      )}
      {error !== null && <pre style={{ whiteSpace: "pre-wrap", color: "red" }}>{error}</pre>}
    </section>
  );
}
