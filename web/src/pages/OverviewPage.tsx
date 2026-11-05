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
  const failures = subscriptions?.filter((entry) => entry.state === "error").length ?? 0;
  const lastRefresh = subscriptions?.reduce<string | null>((latest, entry) => {
    if (!entry.last_refresh_at) return latest;
    if (!latest) return entry.last_refresh_at;
    return entry.last_refresh_at > latest ? entry.last_refresh_at : latest;
  }, null);

  const stateLabel = health ? KERNEL_STATE_LABEL[health.state] : "未知";
  const summary = health
    ? `内核${stateLabel} · 当前配置 ${health.active_config || "—"} · ${subscriptions?.length ?? 0} 份订阅，${failures} 个失败`
    : "正在读取内核状态…";

  return (
    <>
      <div className="mb-6">
        <h1 className="font-serif text-[30px] leading-tight font-semibold">总览</h1>
        <p className="mt-1.5 text-sm text-ink-2">{summary}</p>
      </div>

      <ErrorBanner banner={banner} onDismiss={clear} />

      <div className="grid grid-cols-1 items-start gap-4.5 md:grid-cols-12">
        <Card className="md:col-span-7">
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
          <CardContent>
            <dl className="divide-y divide-line-soft text-[13.5px]">
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">自动拉起</dt>
                <dd>{health ? `${health.respawns} 次` : "—"}</dd>
              </div>
              <div className="flex justify-between gap-3 py-2.5">
                <dt className="text-ink-3">当前配置</dt>
                <dd className="font-mono text-[12.5px]">{health?.active_config || "—"}</dd>
              </div>
            </dl>
            <div className="mt-4 flex flex-wrap gap-2">
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

        <Card className="md:col-span-5">
          <CardHeader>
            <CardTitle>当前配置</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="font-serif text-[26px] leading-none">{health?.active_config || "—"}</p>
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
                <dd className="text-ink-3">
                  {activeConfig?.kind === "subscription-derived" ? activeConfig.name : "—"}
                </dd>
              </div>
            </dl>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                variant="primary"
                disabled={!health?.active_config}
                onClick={() => navigate(`/configs/${encodeURIComponent(health?.active_config ?? "")}`)}
              >
                <PenLine />
                编辑
              </Button>
              <Button onClick={() => navigate("/configs")}>切换配置</Button>
            </div>
          </CardContent>
        </Card>

        <Card className="md:col-span-7">
          <CardHeader>
            <CardTitle>订阅概览</CardTitle>
            <Badge className="ml-auto">{subscriptions?.length ?? 0} 份</Badge>
          </CardHeader>
          <CardContent>
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
            <div className="mt-4 flex flex-wrap gap-2">
              <Button onClick={() => navigate("/subscriptions")}>查看订阅</Button>
            </div>
          </CardContent>
        </Card>

        <Card className="md:col-span-5">
          <CardHeader>
            <CardTitle>运行面板</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-[13px] text-ink-2">
              节点选择、延迟测试、连接与流量，由上游 metacubexd 提供，在新标签页打开。
            </p>
            <div className="mt-4">
              <Button variant="primary" asChild>
                <a href="/ui/" target="_blank" rel="noreferrer">
                  打开运行面板
                  <ExternalLink />
                </a>
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
