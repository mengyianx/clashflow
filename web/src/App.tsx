import { lazy, Suspense } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { Toaster } from "@/components/Toaster";
import { useTheme } from "@/hooks/useTheme";
import { SessionProvider, useSession } from "@/lib/session";
import { ConfigsPage } from "@/pages/ConfigsPage";
import { LoginPage } from "@/pages/LoginPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { SubscriptionsPage } from "@/pages/SubscriptionsPage";

// CodeMirror is the heaviest dependency and only the editor route needs it.
const EditorPage = lazy(() =>
  import("@/pages/EditorPage").then((module) => ({ default: module.EditorPage })),
);

function AppRoutes() {
  const session = useSession();
  const { theme, toggle } = useTheme();

  if (session.status === "checking") {
    return <div className="grid min-h-screen place-items-center text-sm text-ink-3">正在连接面板…</div>;
  }

  return (
    <HashRouter>
      <Toaster theme={theme} />
      <Routes>
        {session.status === "signed-in" ? (
          <Route element={<AppShell theme={theme} onToggleTheme={toggle} />}>
            <Route path="/overview" element={<OverviewPage />} />
            <Route path="/configs" element={<ConfigsPage />} />
            <Route
              path="/configs/:name"
              element={
                <Suspense fallback={<p className="text-sm text-ink-3">正在加载编辑器…</p>}>
                  <EditorPage />
                </Suspense>
              }
            />
            <Route path="/subscriptions" element={<SubscriptionsPage />} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Route>
        ) : (
          <Route path="*" element={<LoginPage />} />
        )}
      </Routes>
    </HashRouter>
  );
}

export default function App() {
  return (
    <SessionProvider>
      <AppRoutes />
    </SessionProvider>
  );
}
