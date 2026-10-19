import { useState } from "react";

// Ticket #17: the Management Panel login. POSTs the password to
// /api/panel/auth/login; on success the server sets the HttpOnly session
// cookie and the shell proceeds. A wrong password is refused with 401.
export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch("/api/panel/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (resp.ok) {
        setPassword("");
        onLogin();
      } else {
        setError(resp.status === 401 ? "Wrong password" : `Login failed (HTTP ${resp.status})`);
      }
    } catch {
      setError("Login failed: panel unreachable");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main style={{ fontFamily: "system-ui", margin: "4rem auto", maxWidth: 320 }}>
      <h1>ClashFlow</h1>
      <h2>Login</h2>
      <p>
        <input
          type="password"
          value={password}
          placeholder="Panel password"
          autoComplete="current-password"
          onChange={(event) => setPassword(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void submit();
          }}
          style={{ width: "100%" }}
        />
      </p>
      <p>
        <button onClick={() => void submit()} disabled={busy || password === ""}>
          Login
        </button>
      </p>
      {error !== null && <p style={{ color: "red" }}>{error}</p>}
    </main>
  );
}
