import { ArrowLeft, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ErrorBanner, useErrorBanner } from "@/components/ErrorBanner";
import { YamlEditor } from "@/components/YamlEditor";
import { Alert } from "@/components/ui/alert";
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
import { ApiError, api, type RuntimeConfig, type RuntimeDiffKey } from "@/lib/api";

/** The Runtime Config view: exactly what the Kernel is running, read-only,
 * beside the per-key diff against its Source Config. There is no save button
 * on purpose — the Source Config and the Overlay are the two writable truths. */

function display(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function RuntimePage() {
  const { name = "" } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const { banner, show, clear } = useErrorBanner();
  const [runtime, setRuntime] = useState<RuntimeConfig | null>(null);
  const [diff, setDiff] = useState<RuntimeDiffKey[] | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!name) return;
    setBusy(true);
    try {
      const [nextRuntime, nextDiff] = await Promise.all([
        api.configs.runtime(name),
        api.configs.runtimeDiff(name),
      ]);
      setRuntime(nextRuntime);
      setDiff(nextDiff.keys);
      setMissing(false);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setRuntime(null);
        setDiff(null);
        setMissing(true);
      } else {
        show(error);
      }
    } finally {
      setBusy(false);
    }
  }, [name, show]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <div className="mb-5">
        <h1 className="font-serif text-[30px] leading-tight font-semibold">{name} · 运行配置</h1>
        <p className="mt-1.5 text-sm text-ink-2">
          内核实际运行的文件，由源配置与网关覆盖渲染而成 · 只读
          {runtime ? (
            <Badge
              variant={runtime.current ? "ok" : "warn"}
              className="ml-2 align-middle"
            >
              {runtime.current ? "与源配置一致" : "已过期"}
            </Badge>
          ) : null}
        </p>
      </div>

      <ErrorBanner banner={banner} onDismiss={clear} />

      <div className="mb-4 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => navigate(`/configs/${encodeURIComponent(name)}`)}>
          <ArrowLeft />
          返回编辑器
        </Button>
        <Button size="sm" onClick={() => void load()} disabled={busy}>
          <RefreshCw />
          刷新
        </Button>
        <Button
          size="sm"
          className="ml-auto"
          onClick={() => navigate("/overlay")}
          disabled={busy}
        >
          调整网关覆盖
        </Button>
      </div>

      {missing ? (
        <Alert variant="info" title="这份配置还没有运行配置" className="mb-4">
          它会在保存这份配置或把它切换为当前配置时渲染生成；源配置本身不受影响。
        </Alert>
      ) : null}

      {diff ? (
        <Card className="mb-4 overflow-hidden">
          <div className="border-b border-line-soft px-4 py-3">
            <b className="text-sm">运行配置与源配置的差异</b>
            <span className="ml-2 text-xs text-ink-3">
              {diff.length === 0 ? "没有差异" : `${diff.length} 个键被网关覆盖改写或新增`}
            </span>
          </div>
          {diff.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[34%]">键</TableHead>
                  <TableHead>源配置</TableHead>
                  <TableHead>内核运行的值</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {diff.map((row) => (
                  <TableRow key={row.path}>
                    <TableCell className="font-mono text-xs">{row.path}</TableCell>
                    <TableCell className="font-mono text-xs break-all text-ink-2">
                      {row.in_source ? display(row.source) : "—（源中没有）"}
                    </TableCell>
                    <TableCell className="font-mono text-xs break-all text-ink">
                      {row.in_runtime ? display(row.runtime) : "—（运行配置中没有）"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : null}
        </Card>
      ) : null}

      {runtime ? (
        <div className="overflow-hidden rounded-[var(--radius-card)] border border-line-soft bg-surface shadow-[var(--shadow-card)]">
          <div className="flex items-center gap-2.5 border-b border-line-soft px-4 py-3">
            <Badge>YAML</Badge>
            <span className="hidden text-xs text-ink-3 md:inline">
              只读 · 要改内容请编辑源配置或网关覆盖
            </span>
          </div>
          <div className="h-[calc(100vh-430px)] min-h-[300px]">
            <YamlEditor value={runtime.content} readOnly />
          </div>
        </div>
      ) : null}
    </>
  );
}
