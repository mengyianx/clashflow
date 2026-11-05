import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { api, ApiError, type ServiceHealth } from "./api";

/** `checking` is the first load; the shell renders nothing until it resolves. */
export type SessionStatus = "checking" | "signed-in" | "signed-out";

export interface SessionValue {
  status: SessionStatus;
  health: ServiceHealth | null;
  /** The panel itself stopped answering (container down, network gone). */
  connectionLost: boolean;
  recheck: () => Promise<void>;
  markSignedOut: () => void;
}

const SessionContext = createContext<SessionValue | null>(null);

const POLL_INTERVAL_MS = 5000;

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>("checking");
  const [health, setHealth] = useState<ServiceHealth | null>(null);
  const [connectionLost, setConnectionLost] = useState(false);

  // One poll for the whole shell: the health probe doubles as the auth check
  // (it answers 401 for an expired session), so pages never poll separately.
  const recheck = useCallback(async () => {
    try {
      const next = await api.health();
      setHealth(next);
      setConnectionLost(false);
      setStatus("signed-in");
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setHealth(null);
        setStatus("signed-out");
        return;
      }
      setConnectionLost(true);
    }
  }, []);

  useEffect(() => {
    void recheck();
    const timer = window.setInterval(() => void recheck(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [recheck]);

  const markSignedOut = useCallback(() => {
    setHealth(null);
    setStatus("signed-out");
  }, []);

  return (
    <SessionContext.Provider value={{ status, health, connectionLost, recheck, markSignedOut }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used below SessionProvider");
  return value;
}
