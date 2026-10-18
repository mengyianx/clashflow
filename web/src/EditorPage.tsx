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
// Ticket #13: opening a Subscription-derived Config shows the overwrite
// warning (the next refresh replaces your edits) with the one-step
// copy-to-Hand-written escape hatch; Hand-written Configs never see it.
export function EditorPage({
  configName,
  onOpen,
}: {
  configName: string;
  onOpen?: (name: string) => void;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [kind, setKind] = useState<string>("");
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
        setKind(body.kind);
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

  // One-step escape hatch for the overwrite warning: one call to the copy
  // endpoint produces an independent Hand-written Config with the same
  // content (refreshes never touch it), and the editor opens that copy.
  const copyToHandwritten = async () => {
    for (let attempt = 1; attempt <= 5; attempt++) {
      const target = attempt === 1 ? `${configName}-copy` : `${configName}-copy-${attempt}`;
      const resp = await fetch(`/api/panel/configs/${encodeURIComponent(configName)}/copy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: target }),
      });
      if (resp.ok) {
        const body = (await resp.json()) as { name: string };
        setError(null);
        setSaved(false);
        onOpen?.(body.name);
        return;
      }
      if (resp.status !== 409) {
        await reportFailure(resp);
        return;
      }
    }
    setError(`copy refused: no free name near "${configName}-copy" — delete or rename one first`);
  };

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
          {kind === "subscription-derived" && (
            <p
              data-testid="subscription-overwrite-warning"
              style={{ borderLeft: "4px solid orange", paddingLeft: 8, background: "#fff6e5" }}
            >
              <strong>Subscription-derived:</strong> the next refresh will overwrite your edits.{" "}
              <button onClick={() => void copyToHandwritten()}>Copy to Hand-written Config</button>
            </p>
          )}
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
