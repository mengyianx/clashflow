import { MoreHorizontal, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorBanner, useErrorBanner } from "@/components/ErrorBanner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, type SubscriptionEntry } from "@/lib/api";
import { SUBSCRIPTION_FAILURE_CLASS_LABEL, SUBSCRIPTION_STATE_LABEL } from "@/lib/messages";
import { formatSummary, formatTimestamp } from "@/lib/utils";

interface Draft {
  userAgent: string;
  interval: string;
}

const stateVariant = (entry: SubscriptionEntry) =>
  entry.state === "ok" ? "ok" : entry.state === "error" ? "bad" : "mute";

export function SubscriptionsPage() {
  const { banner, show, showText, clear } = useErrorBanner();
  const [subscriptions, setSubscriptions] = useState<SubscriptionEntry[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.subscriptions.list();
      setSubscriptions(next);
      setDrafts(
        Object.fromEntries(
          next.map((entry) => [
            entry.name,
            {
              userAgent: entry.user_agent ?? "",
              interval: entry.update_interval_minutes === null ? "" : String(entry.update_interval_minutes),
            },
          ]),
        ),
      );
    } catch (error) {
      show(error);
    }
  }, [show]);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async (event: FormEvent) => {
    event.preventDefault();
    setBusy("add");
    clear();
    try {
      await api.subscriptions.add(name.trim(), url.trim());
      toast.success(`已导入订阅 ${name.trim()}`);
      setName("");
      setUrl("");
      await load();
    } catch (error) {
      show(error);
    } finally {
      setBusy(null);
    }
  };

  const saveSettings = async (entry: SubscriptionEntry) => {
    const draft = drafts[entry.name] ?? { userAgent: "", interval: "" };
    const interval = draft.interval.trim();
    if (interval !== "" && !/^\d+$/.test(interval)) {
      showText("更新间隔必须是整数分钟", `当前输入：${interval}`);
      return;
    }
    setBusy(entry.name);
    clear();
    try {
      await api.subscriptions.updateSettings(entry.name, {
        user_agent: draft.userAgent.trim() === "" ? null : draft.userAgent.trim(),
        update_interval_minutes: interval === "" ? null : Number(interval),
      });
      toast.success("设置已保存");
      await load();
    } catch (error) {
      show(error);
    } finally {
      setBusy(null);
    }
  };

  const refresh = async (entry: SubscriptionEntry) => {
    setBusy(entry.name);
    clear();
    try {
      await api.subscriptions.refresh(entry.name);
      toast.success(`已刷新 ${entry.name}`);
    } catch (error) {
      show(error);
    } finally {
      await load();
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!pendingDelete) return;
    setBusy(pendingDelete);
    try {
      await api.subscriptions.remove(pendingDelete);
      toast.success(`已删除订阅 ${pendingDelete}`);
      setPendingDelete(null);
      await load();
    } catch (error) {
      show(error);
    } finally {
      setBusy(null);
    }
  };

  const updateDraft = (entryName: string, patch: Partial<Draft>) => {
    setDrafts((current) => ({
      ...current,
      [entryName]: { ...(current[entryName] ?? { userAgent: "", interval: "" }), ...patch },
    }));
  };

  const failureLine = (entry: SubscriptionEntry) => {
    if (entry.state !== "error" || !entry.last_error) return null;
    return (
      <div className="mt-1">
        {entry.last_error_class ? (
          <div className="text-[11.5px] font-medium text-bad">
            {SUBSCRIPTION_FAILURE_CLASS_LABEL[entry.last_error_class]}
          </div>
        ) : null}
        <p className="font-mono text-[11.5px] break-words text-bad">{entry.last_error}</p>
      </div>
    );
  };

  return (
    <>
      <div className="mb-5">
        <h1 className="font-serif text-[30px] leading-tight font-semibold">订阅</h1>
        <p className="mt-1.5 text-sm text-ink-2">
          {subscriptions
            ? `${subscriptions.length} 份订阅 · ${subscriptions.filter((entry) => entry.state === "error").length} 个失败`
            : "正在读取…"}
        </p>
      </div>

      <ErrorBanner banner={banner} onDismiss={clear} />

      <form onSubmit={(event) => void add(event)} className="mb-4 flex flex-wrap gap-2">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="名称"
          className="max-w-[200px]"
        />
        <Input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://example.com/sub"
          className="max-w-[320px] font-mono text-xs"
        />
        <Button
          type="submit"
          variant="primary"
          disabled={busy === "add" || name.trim() === "" || url.trim() === ""}
        >
          <Plus />
          导入订阅
        </Button>
      </form>

      <Card className="hidden px-2 py-4.5 md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>配置摘要</TableHead>
              <TableHead>上次刷新</TableHead>
              <TableHead>User-Agent</TableHead>
              <TableHead>间隔（分钟）</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(subscriptions ?? []).map((entry) => {
              const draft = drafts[entry.name] ?? { userAgent: "", interval: "" };
              return (
                <TableRow key={entry.name}>
                  <TableCell>
                    <b className="block font-medium">{entry.name}</b>
                    <span className="block max-w-[220px] truncate font-mono text-[11.5px] text-ink-3" title={entry.url}>
                      {entry.url}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Badge variant={stateVariant(entry)} dot>
                      {SUBSCRIPTION_STATE_LABEL[entry.state]}
                    </Badge>
                    {failureLine(entry)}
                  </TableCell>
                  <TableCell className="text-xs text-ink-3 whitespace-nowrap">
                    {formatSummary(entry.summary)}
                  </TableCell>
                  <TableCell className="text-ink-3 whitespace-nowrap">
                    {formatTimestamp(entry.last_refresh_at)}
                  </TableCell>
                  <TableCell>
                    <Input
                      value={draft.userAgent}
                      onChange={(event) => updateDraft(entry.name, { userAgent: event.target.value })}
                      placeholder="默认"
                      aria-label={`${entry.name} 的 User-Agent`}
                      className="w-[150px] font-mono text-xs"
                    />
                  </TableCell>
                  <TableCell>
                    <Input
                      value={draft.interval}
                      onChange={(event) => updateDraft(entry.name, { interval: event.target.value })}
                      placeholder="默认"
                      inputMode="numeric"
                      aria-label={`${entry.name} 的更新间隔`}
                      className="w-[80px]"
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex justify-end gap-1.5">
                      <Button
                        size="sm"
                        onClick={() => void saveSettings(entry)}
                        disabled={busy === entry.name}
                      >
                        保存设置
                      </Button>
                      <Button size="sm" onClick={() => void refresh(entry)} disabled={busy === entry.name}>
                        <RefreshCw />
                        刷新
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => setPendingDelete(entry.name)}
                        aria-label={`删除 ${entry.name}`}
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>

      <div className="flex flex-col gap-2.5 md:hidden">
        {(subscriptions ?? []).map((entry) => (
          <Card key={entry.name} className="p-3.5">
            <div className="flex items-center gap-2">
              <b className="text-sm">{entry.name}</b>
              <Badge variant={stateVariant(entry)} dot>
                {SUBSCRIPTION_STATE_LABEL[entry.state]}
              </Badge>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="icon" variant="ghost" className="ml-auto" aria-label="更多操作">
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={() => void saveSettings(entry)}>
                    保存设置
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => void refresh(entry)}>刷新</DropdownMenuItem>
                  <DropdownMenuItem destructive onSelect={() => setPendingDelete(entry.name)}>
                    删除
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <p className="mt-1 font-mono text-[11.5px] break-all text-ink-3">{entry.url}</p>
            <p className="mt-1 text-xs text-ink-3">
              {formatSummary(entry.summary)} · 上次刷新 {formatTimestamp(entry.last_refresh_at)} · 间隔{" "}
              {entry.update_interval_minutes === null ? "默认" : `${entry.update_interval_minutes} 分钟`}
            </p>
            {failureLine(entry)}
            <div className="mt-2.5 flex gap-1.5">
              <Input
                value={drafts[entry.name]?.userAgent ?? ""}
                onChange={(event) => updateDraft(entry.name, { userAgent: event.target.value })}
                placeholder="User-Agent（默认）"
                aria-label={`${entry.name} 的 User-Agent`}
                className="font-mono text-xs"
              />
              <Input
                value={drafts[entry.name]?.interval ?? ""}
                onChange={(event) => updateDraft(entry.name, { interval: event.target.value })}
                placeholder="间隔"
                inputMode="numeric"
                aria-label={`${entry.name} 的更新间隔`}
                className="w-20"
              />
            </div>
          </Card>
        ))}
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`删除订阅「${pendingDelete ?? ""}」？`}
        description="该订阅的配置会一并删除，无法恢复；如果它正在运行，操作会被拒绝。"
        pending={busy !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        onConfirm={() => void remove()}
      />
    </>
  );
}
