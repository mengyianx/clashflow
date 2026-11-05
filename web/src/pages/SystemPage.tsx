import { Download, History, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ErrorBanner, useErrorBanner } from "@/components/ErrorBanner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, type UpdateComponent, type UpdateState } from "@/lib/api";
import { formatTimestamp } from "@/lib/utils";

const LABEL: Record<UpdateComponent, string> = {
  kernel: "内核（mihomo）",
  metacubexd: "Metacubexd",
  zashboard: "Zashboard",
};

const NOTE: Record<UpdateComponent, string> = {
  kernel: "更新会重启内核，失败自动回滚",
  metacubexd: "静态文件热切换，无需重启",
  zashboard: "静态文件热切换，无需重启",
};

export function SystemPage() {
  const { banner, show, clear } = useErrorBanner();
  const [updates, setUpdates] = useState<UpdateState[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState<UpdateComponent | null>(null);

  const load = useCallback(async () => {
    try {
      setUpdates(await api.updates.list());
    } catch (error) {
      show(error);
    }
  }, [show]);

  useEffect(() => {
    void load();
  }, [load]);

  const check = async () => {
    setChecking(true);
    clear();
    try {
      setUpdates(await api.updates.check());
      toast.success("已检查更新");
    } catch (error) {
      show(error);
    } finally {
      setChecking(false);
    }
  };

  const run = async (action: "update" | "rollback", component: UpdateComponent) => {
    setBusy(component);
    clear();
    try {
      setUpdates(await api.updates[action](component));
      toast.success(
        action === "update" ? `${LABEL[component]} 已更新` : `${LABEL[component]} 已回滚`,
      );
    } catch (error) {
      show(error);
      await load();
    } finally {
      setBusy(null);
    }
  };

  const latestLine = (row: UpdateState) => {
    if (row.error) return <span className="text-bad">{row.error}</span>;
    if (!row.latest) return <span className="text-ink-3">未检查</span>;
    return row.latest === row.current ? (
      <span className="text-ink-3">已是最新</span>
    ) : (
      <b>{row.latest}</b>
    );
  };

  const actions = (row: UpdateState) => (
    <div className="flex justify-end gap-1.5">
      <Button
        size="sm"
        variant="primary"
        disabled={busy !== null || !row.latest || row.latest === row.current}
        onClick={() => void run("update", row.component)}
      >
        <Download />
        {busy === row.component ? "更新中…" : "更新"}
      </Button>
      <Button
        size="sm"
        disabled={busy !== null || !row.previous}
        onClick={() => void run("rollback", row.component)}
      >
        <History />
        回滚
      </Button>
    </div>
  );

  return (
    <>
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <div>
          <h1 className="font-serif text-[30px] leading-tight font-semibold">系统</h1>
          <p className="mt-1.5 text-sm text-ink-2">
            版本与更新 · 手动检查 GitHub 最新发布，更新包保存在 /data，可随时回滚
          </p>
        </div>
        <Button className="ml-auto" onClick={() => void check()} disabled={checking || busy !== null}>
          <RefreshCw className={checking ? "animate-spin" : undefined} />
          {checking ? "检查中…" : "检查更新"}
        </Button>
      </div>

      <ErrorBanner banner={banner} onDismiss={clear} />

      <Card className="hidden px-2 py-4.5 md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>组件</TableHead>
              <TableHead>当前版本</TableHead>
              <TableHead>来源</TableHead>
              <TableHead>最新版本</TableHead>
              <TableHead>上次检查</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(updates ?? []).map((row) => (
              <TableRow key={row.component}>
                <TableCell>
                  <b className="block font-medium">{LABEL[row.component]}</b>
                  <span className="block text-xs text-ink-3">{NOTE[row.component]}</span>
                </TableCell>
                <TableCell className="font-mono text-xs">{row.current}</TableCell>
                <TableCell>
                  {row.source === "runtime" ? (
                    <Badge variant="warn">已更新</Badge>
                  ) : (
                    <Badge>镜像</Badge>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs">{latestLine(row)}</TableCell>
                <TableCell className="text-ink-3 whitespace-nowrap">
                  {formatTimestamp(row.checked_at)}
                </TableCell>
                <TableCell>{actions(row)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <div className="flex flex-col gap-2.5 md:hidden">
        {(updates ?? []).map((row) => (
          <Card key={row.component} className="p-3.5">
            <div className="flex items-center gap-2">
              <b className="text-sm">{LABEL[row.component]}</b>
              {row.source === "runtime" ? <Badge variant="warn">已更新</Badge> : <Badge>镜像</Badge>}
              <span className="ml-auto font-mono text-xs text-ink-3">{row.current}</span>
            </div>
            <p className="mt-1 text-xs text-ink-3">{NOTE[row.component]}</p>
            <p className="mt-1 font-mono text-xs">最新：{latestLine(row)}</p>
            <div className="mt-2.5">{actions(row)}</div>
          </Card>
        ))}
      </div>
    </>
  );
}
