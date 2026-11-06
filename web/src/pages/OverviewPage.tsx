import { ExternalLink, PenLine, Power, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ErrorBanner, useErrorBanner } from "@/components/ErrorBanner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type ConfigEntry, type SubscriptionEntry } from "@/lib/api";
import { CONFIG_KIND_LABEL, KERNEL_STATE_LABEL } from "@/lib/messages";
import { useSession } from "@/lib/session";
import { formatTimestamp } from "@/lib/utils";

export function OverviewPage() {
  const { health, recheck } = useSession();
  const { banner, show, clear } = useErrorBanner();
  const navigate = useNavigate();
  const [configs, setConfigs] = useState<ConfigEntry[] | null>(null);
  const [subscriptions, setSubscriptions] = useState<SubscriptionEntry[] | null>(null);
  const [busy, setBusy] = useState<"start" | "stop" | "restart" | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextConfigs, nextSubscriptions] = await Promise.all([
        api.configs.list(),
        api.subscriptions.list(),
      ]);
      setConfigs(nextConfigs);
      setSubscriptions(nextSubscriptions);
    } catch (error) {
      show(error);
    }
  }, [show]);

  useEffect(() => {
    void load();
  }, [load]);

  const runServiceAction = async (action: "start" | "stop" | "restart") => {
    setBusy(action);
    clear();
    try {
      await api.service[action]();
      await recheck();
    } catch (error) {
      show(error);
    } finally {
      setBusy(null);
    }
  };

  const activeConfig = configs?.find((config) => config.active) ?? null;
  // The recorded Active Config (the list) wins over the polled health string:
  // health refreshes every 5 s, so right after a switch elsewhere it can still
  // name the previous Config while the list already names the new one.
  const activeName = activeConfig?.name ?? health?.active_config ?? "";
  const failures = subscriptions?.filter((entry) => entry.state === "error").length ?? 0;
  const lastRefresh = subscriptions?.reduce<string | null>((latest, entry) => {
    if (!entry.last_refresh_at) return latest;
    if (!latest) return entry.last_refresh_at;
    return entry.last_refresh_at > latest ? entry.last_refresh_at : latest;
  }, null);

  const stateLabel = health ? KERNEL_STATE_LABEL[health.state] : "未知";
  const summary = health
    ? `内核${stateLabel} · 当前配置 ${activeName || "—"} · ${subscriptions?.length ?? 0} 份订阅，${failures} 个失败`
    : "正在读取内核状态…";

  return (
    <>
      <div className="mb-6">
        <h1 className="font-serif text-[30px] leading-tight font-semibold">总览</h1>
        <p className="mt-1.5 text-sm text-ink-2">{summary}</p>
      </div>

      <ErrorBanner banner={banner} onDismiss={clear} />

      <div className="grid grid-cols-1 gap-4.5 md:grid-cols-12">
        <Card className="flex flex-col md:col-span-7">
          <CardHeader>
            <CardTitle>内核状态</CardTitle>
            <Badge
              variant={health?.kernel === "up" ? "ok" : "bad"}
              dot
              className="ml-auto"
            >
              {stateLabel}
            </Badge>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col">
            <dl className="divide-y divide-line-soft text-[13.5px]">
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">自动拉起</dt>
                <dd>{health ? `${health.respawns} 次` : "—"}</dd>
              </div>
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">当前配置</dt>
                <dd className="font-mono text-[12.5px]">{activeName || "—"}</dd>
              </div>
            </dl>
            <div className="mt-auto flex flex-wrap gap-2 pt-4">
              {health?.kernel === "up" ? (
                <>
                  <Button onClick={() => void runServiceAction("stop")} disabled={busy !== null}>
                    <Power />
                    停止
                  </Button>
                  <Button onClick={() => void runServiceAction("restart")} disabled={busy !== null}>
                    <RefreshCw />
                    重启
                  </Button>
                </>
              ) : (
                <Button
                  variant="primary"
                  onClick={() => void runServiceAction("start")}
                  disabled={busy !== null}
                >
                  <Power />
                  启动
                </Button>
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="flex flex-col md:col-span-5">
          <CardHeader>
            <CardTitle>当前配置</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col">
            <p className="font-serif text-[26px] leading-none">{activeName || "—"}</p>
            <dl className="mt-3 divide-y divide-line-soft text-[13.5px]">
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">类型</dt>
                <dd>{activeConfig ? CONFIG_KIND_LABEL[activeConfig.kind] : "—"}</dd>
              </div>
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">文件</dt>
                <dd className="font-mono text-[12.5px]">{activeConfig?.filename ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">订阅来源</dt>
                <dd className="text-ink-3">{activeConfig?.subscription ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">内容</dt>
                <dd className="text-ink-3">
                  {activeConfig?.summary
                    ? `${activeConfig.summary.nodes} 节点 / ${activeConfig.summary.groups} 组 / ${activeConfig.summary.rules} 规则`
                    : "—"}
                </dd>
              </div>
              {activeConfig?.summary?.age_key_missing ? (
                <div className="flex justify-between gap-3 py-2.5">
                  <dt className="text-ink-3">Age key</dt>
                  <dd>
                    <Badge variant="warn">缺失</Badge>
                  </dd>
                </div>
              ) : null}
            </dl>
            <div className="mt-auto flex flex-wrap gap-2 pt-4">
              <Button
                variant="primary"
                disabled={!activeName}
                onClick={() => navigate(`/configs/${encodeURIComponent(activeName)}`)}
              >
                <PenLine />
                编辑
              </Button>
              <Button onClick={() => navigate("/configs")}>切换配置</Button>
            </div>
          </CardContent>
        </Card>

        <Card className="flex flex-col md:col-span-5">
          <CardHeader>
            <CardTitle>订阅概览</CardTitle>
            <Badge className="ml-auto">{subscriptions?.length ?? 0} 份</Badge>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col">
            <dl className="divide-y divide-line-soft text-[13.5px]">
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">最近刷新</dt>
                <dd>{formatTimestamp(lastRefresh ?? null)}</dd>
              </div>
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">失败</dt>
                <dd>{subscriptions ? `${failures} 个` : "—"}</dd>
              </div>
            </dl>
            <div className="mt-auto flex flex-wrap gap-2 pt-4">
              <Button onClick={() => navigate("/subscriptions")}>查看订阅</Button>
            </div>
          </CardContent>
        </Card>

        <Card className="flex flex-col md:col-span-7">
          <CardHeader>
            <CardTitle>运行面板</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col">
            <div className="divide-y divide-line-soft">
              <div className="flex items-center gap-3 py-2.5">
                <div className="min-w-0">
                  <b className="text-[13.5px] font-medium">Metacubexd</b>
                  <p className="text-xs text-ink-3">节点选择、延迟测试、连接与流量</p>
                </div>
                <Button size="sm" className="ml-auto" asChild>
                  <a href="/ui/" target="_blank" rel="noreferrer">
                    打开
                    <ExternalLink />
                  </a>
                </Button>
              </div>
              <div className="flex items-center gap-3 py-2.5">
                <div className="min-w-0">
                  <b className="text-[13.5px] font-medium">Zashboard</b>
                  <p className="text-xs text-ink-3">节点卡片、连接表格与规则查询</p>
                </div>
                <Button size="sm" className="ml-auto" asChild>
                  <a href="/zui/" target="_blank" rel="noreferrer">
                    打开
                    <ExternalLink />
                  </a>
                </Button>
              </div>
            </div>
            <p className="mt-auto pt-3 text-xs text-ink-3">
              两者都是上游运行面板，在新标签页打开；已预置本面板的 /api 反代，无需填端点。
            </p>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
