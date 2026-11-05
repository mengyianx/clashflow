import { ChevronRight, Eye, EyeOff, Lock } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { ErrorBanner, useErrorBanner } from "@/components/ErrorBanner";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

export function LoginPage() {
  const { recheck } = useSession();
  const { banner, show, clear } = useErrorBanner();
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    document.title = "ClashFlow · 登录";
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    clear();
    try {
      await api.login(password);
      await recheck();
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen place-items-center px-6">
      <div className="w-full max-w-[380px]">
        <h1 className="text-center font-serif text-[34px] font-semibold">欢迎回来</h1>
        <p className="mt-2 mb-7 text-center text-[13.5px] text-ink-2">ClashFlow · 管理面板</p>

        <form onSubmit={(event) => void submit(event)}>
          <div className="relative">
            <Lock className="absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-ink-3" />
            <input
              type={reveal ? "text" : "password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="面板密码"
              autoFocus
              autoComplete="current-password"
              className="h-12 w-full rounded-[var(--radius-panel)] border border-line bg-surface pr-11 pl-10 text-sm text-ink placeholder:text-ink-3 focus:border-accent focus:ring-4 focus:ring-[var(--tint)] focus:outline-none"
            />
            <button
              type="button"
              onClick={() => setReveal((current) => !current)}
              aria-label={reveal ? "隐藏密码" : "显示密码"}
              className="absolute top-1/2 right-2 -translate-y-1/2 rounded-[var(--radius-control)] p-2 text-ink-3 hover:bg-hover hover:text-ink"
            >
              {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </button>
          </div>
          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="mt-3 w-full"
            disabled={busy || password.length === 0}
          >
            {busy ? "登录中…" : "进入"}
            <ChevronRight className="size-4" />
          </Button>
        </form>

        <ErrorBanner banner={banner} onDismiss={clear} />

        <p className="mt-7 text-center text-xs text-ink-3">
          仅限局域网访问 · 代理端口对局域网设备免认证
        </p>
      </div>
    </div>
  );
}
