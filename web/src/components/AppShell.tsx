import {
  ExternalLink,
  Home,
  Layers,
  LogOut,
  Moon,
  Rss,
  Settings2,
  SlidersHorizontal,
  Sun,
} from "lucide-react";
import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { BrandMark } from "@/components/BrandMark";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { cn } from "@/lib/utils";
import type { Theme } from "@/hooks/useTheme";

const NAV = [
  { to: "/overview", label: "总览", Icon: Home },
  { to: "/configs", label: "配置", Icon: SlidersHorizontal },
  { to: "/overlay", label: "网关覆盖", Icon: Layers },
  { to: "/subscriptions", label: "订阅", Icon: Rss },
  { to: "/system", label: "系统", Icon: Settings2 },
];

const navClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex items-center gap-2.5 rounded-[var(--radius-control)] px-3 py-2 text-sm text-ink-2",
    "hover:bg-hover hover:text-ink",
    isActive && "bg-surface font-medium text-ink shadow-sm",
  );

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2 pb-5">
      <BrandMark />
      <div className="leading-tight">
        <b className="block text-[15px] tracking-[0.01em]">ClashFlow</b>
        <span className="block text-[11.5px] text-ink-3">管理面板</span>
      </div>
    </div>
  );
}

/** The panel frame: sidebar on wide screens, top bar + bottom tabs on phones.
 * Every page renders inside `<Outlet/>`. */
const TITLES: Array<[prefix: string, label: string]> = [
  ["/configs/", "编辑器"],
  ["/configs", "配置"],
  ["/overlay", "网关覆盖"],
  ["/subscriptions", "订阅"],
  ["/system", "系统"],
  ["/overview", "总览"],
];

export function AppShell({ theme, onToggleTheme }: { theme: Theme; onToggleTheme: () => void }) {
  const { connectionLost, markSignedOut } = useSession();
  const { pathname } = useLocation();

  // The tab keeps one short label per page instead of the long shell title.
  useEffect(() => {
    const label = TITLES.find(([prefix]) => pathname.startsWith(prefix))?.[1] ?? "总览";
    document.title = `ClashFlow · ${label}`;
  }, [pathname]);

  const signOut = async () => {
    try {
      await api.logout();
    } catch {
      // An unreachable panel still ends the local session.
    }
    markSignedOut();
  };

  const themeLabel = theme === "dark" ? "浅色" : "深色";
  const ThemeIcon = theme === "dark" ? Sun : Moon;

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-[236px] flex-none flex-col border-r border-line-soft px-3.5 pt-5 pb-4 md:flex">
        <Brand />
        <nav className="flex flex-col gap-0.5">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink key={to} to={to} className={navClass}>
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
          <div className="mt-3 px-3 pb-1 text-[11px] text-ink-3">运行面板</div>
          <a
            href="/ui/"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2.5 rounded-[var(--radius-control)] px-3 py-2 text-sm text-ink-2 hover:bg-hover hover:text-ink"
          >
            <ExternalLink className="size-4" />
            Metacubexd
          </a>
          <a
            href="/zui/"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2.5 rounded-[var(--radius-control)] px-3 py-2 text-sm text-ink-2 hover:bg-hover hover:text-ink"
          >
            <ExternalLink className="size-4" />
            Zashboard
          </a>
        </nav>
        <div className="mt-auto flex gap-1.5 px-1 pt-1.5">
          <Button variant="ghost" size="sm" className="flex-1 justify-center" onClick={onToggleTheme}>
            <ThemeIcon />
            {themeLabel}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void signOut()} aria-label="退出登录">
            <LogOut />
          </Button>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-line-soft bg-canvas px-4 py-3 md:hidden">
          <div className="flex-1">
            <Brand />
          </div>
          <Button variant="ghost" size="sm" onClick={onToggleTheme} aria-label="切换深浅色">
            <ThemeIcon />
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void signOut()} aria-label="退出登录">
            <LogOut />
          </Button>
        </header>

        <div className="mx-auto w-full max-w-[1180px] px-4 pt-5 pb-24 md:px-9 md:pt-8 md:pb-16">
          {connectionLost ? (
            <Alert variant="info" title="面板连接中断" className="mb-4">
              正在重试，恢复后本页会自动刷新状态。
            </Alert>
          ) : null}
          <Outlet />
        </div>

        <nav className="fixed inset-x-0 bottom-0 z-10 flex border-t border-line-soft bg-surface px-1 pt-1.5 pb-[calc(6px+env(safe-area-inset-bottom))] md:hidden">
          {NAV.map(({ to, label, Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex flex-1 flex-col items-center gap-1 rounded-[var(--radius-control)] py-1.5 text-[11px] text-ink-3",
                  isActive && "text-accent-strong",
                )
              }
            >
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label="运行面板"
                className="flex flex-1 flex-col items-center gap-1 rounded-[var(--radius-control)] py-1.5 text-[11px] text-ink-3"
              >
                <ExternalLink className="size-4" />
                运行面板
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="center" side="top" className="mb-1">
              <DropdownMenuItem asChild>
                <a href="/ui/" target="_blank" rel="noreferrer">
                  Metacubexd
                </a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/zui/" target="_blank" rel="noreferrer">
                  Zashboard
                </a>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </nav>
      </main>
    </div>
  );
}
