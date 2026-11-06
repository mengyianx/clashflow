import { Copy, MoreHorizontal, PenLine, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorBanner, useErrorBanner } from "@/components/ErrorBanner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { api, type ConfigEntry } from "@/lib/api";
import { CONFIG_KIND_LABEL } from "@/lib/messages";
import { formatSummary } from "@/lib/utils";

type CreateMode = { template: boolean } | null;

export function ConfigsPage() {
  const navigate = useNavigate();
  const { banner, show, clear } = useErrorBanner();
  const [configs, setConfigs] = useState<ConfigEntry[] | null>(null);
  const [createMode, setCreateMode] = useState<CreateMode>(null);
  const [newName, setNewName] = useState("");
  const [copySource, setCopySource] = useState<string | null>(null);
  const [copyName, setCopyName] = useState("");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setConfigs(await api.configs.list());
    } catch (error) {
      show(error);
    }
  }, [show]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!createMode) return;
    setBusy(true);
    try {
      await api.configs.create(newName, createMode.template);
      toast.success(`已创建配置 ${newName}`);
      setCreateMode(null);
      setNewName("");
      await load();
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  const copy = async (event: FormEvent) => {
    event.preventDefault();
    if (!copySource) return;
    setBusy(true);
    try {
      await api.configs.copy(copySource, copyName);
      toast.success(`已复制为 ${copyName}`);
      setCopySource(null);
      setCopyName("");
      await load();
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  const activate = async (name: string) => {
    setBusy(true);
    try {
      await api.configs.activate(name);
      toast.success(`已切换到 ${name}`);
      await load();
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!pendingDelete) return;
    setBusy(true);
    try {
      await api.configs.remove(pendingDelete);
      toast.success(`已删除 ${pendingDelete}`);
      setPendingDelete(null);
      await load();
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  const rowActions = (config: ConfigEntry) => (
    <>
      {config.active ? null : (
        <Button size="sm" onClick={() => void activate(config.name)} disabled={busy}>
          设为当前
        </Button>
      )}
      <Button size="sm" onClick={() => navigate(`/configs/${encodeURIComponent(config.name)}`)}>
        <PenLine />
        编辑
      </Button>
      <Button size="sm" onClick={() => setCopySource(config.name)}>
        <Copy />
        复制
      </Button>
      {config.active || config.kind === "subscription-derived" ? null : (
        <Button size="sm" variant="danger" onClick={() => setPendingDelete(config.name)}>
          <Trash2 />
        </Button>
      )}
    </>
  );

  return (
    <>
      <div className="mb-5">
        <h1 className="font-serif text-[30px] leading-tight font-semibold">配置</h1>
        <p className="mt-1.5 text-sm text-ink-2">
          {configs ? `${configs.length} 份配置` : "正在读取…"}
          {configs?.find((config) => config.active)
            ? ` · 当前运行 ${configs.find((config) => config.active)?.name}`
            : ""}
        </p>
      </div>

      <ErrorBanner banner={banner} onDismiss={clear} />

      <div className="mb-4 flex flex-wrap gap-2">
        <Button onClick={() => setCreateMode({ template: false })}>
          <Plus />
          新建空白配置
        </Button>
        <Button onClick={() => setCreateMode({ template: true })}>从模板新建</Button>
      </div>

      <Card className="hidden px-2 py-4.5 md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead>类型</TableHead>
              <TableHead>文件名</TableHead>
              <TableHead>内容</TableHead>
              <TableHead>当前</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(configs ?? []).map((config) => (
              <TableRow key={config.name}>
                <TableCell className="font-medium">{config.name}</TableCell>
                <TableCell className="text-ink-3">{CONFIG_KIND_LABEL[config.kind]}</TableCell>
                <TableCell className="font-mono text-xs text-ink-3">{config.filename}</TableCell>
                <TableCell className="text-xs text-ink-3">
                  {formatSummary(config.summary)}
                </TableCell>
                <TableCell>
                  {config.active ? <Badge variant="ok">当前配置</Badge> : <Badge>—</Badge>}
                </TableCell>
                <TableCell>
                  <div className="flex justify-end gap-1.5">{rowActions(config)}</div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>

      <div className="flex flex-col gap-2.5 md:hidden">
        {(configs ?? []).map((config) => (
          <Card key={config.name} className="p-3.5">
            <div className="flex items-center gap-2">
              <b className="text-sm">{config.name}</b>
              {config.active ? <Badge variant="ok">当前配置</Badge> : null}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="icon" variant="ghost" className="ml-auto" aria-label="更多操作">
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {config.active ? null : (
                    <DropdownMenuItem onSelect={() => void activate(config.name)}>
                      设为当前
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem
                    onSelect={() => navigate(`/configs/${encodeURIComponent(config.name)}`)}
                  >
                    编辑
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setCopySource(config.name)}>
                    复制为手写配置
                  </DropdownMenuItem>
                  {config.active || config.kind === "subscription-derived" ? null : (
                    <DropdownMenuItem destructive onSelect={() => setPendingDelete(config.name)}>
                      删除
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <p className="mt-1 text-xs text-ink-3">
              {CONFIG_KIND_LABEL[config.kind]} · <span className="font-mono">{config.filename}</span>
            </p>
            <p className="mt-1 text-xs text-ink-3">
              {formatSummary(config.summary)}
            </p>
          </Card>
        ))}
      </div>

      <Dialog
        open={createMode !== null}
        onOpenChange={(open) => {
          if (!open) {
            setCreateMode(null);
            setNewName("");
          }
        }}
      >
        <DialogContent>
          <DialogTitle>新建{createMode?.template ? "模板配置" : "空白配置"}</DialogTitle>
          <DialogDescription>
            {createMode?.template
              ? "模板包含 mixed-port 7890、allow-lan 与 GEO 自动更新，创建后即可设为当前配置。"
              : "空白配置以 YAML 空映射开始，保存时由内核校验。"}
          </DialogDescription>
          <form onSubmit={(event) => void create(event)}>
            <Input
              autoFocus
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              placeholder="配置名称"
              className="mt-3"
            />
            <DialogFooter>
              <Button type="button" onClick={() => setCreateMode(null)}>
                取消
              </Button>
              <Button type="submit" variant="primary" disabled={busy || newName.trim() === ""}>
                创建
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog
        open={copySource !== null}
        onOpenChange={(open) => {
          if (!open) {
            setCopySource(null);
            setCopyName("");
          }
        }}
      >
        <DialogContent>
          <DialogTitle>复制「{copySource}」为手写配置</DialogTitle>
          <DialogDescription>
            副本是独立的手写配置，订阅刷新永远不会改写它，可以对它随意编辑。
          </DialogDescription>
          <form onSubmit={(event) => void copy(event)}>
            <Input
              autoFocus
              value={copyName}
              onChange={(event) => setCopyName(event.target.value)}
              placeholder="新配置名称"
              className="mt-3"
            />
            <DialogFooter>
              <Button type="button" onClick={() => setCopySource(null)}>
                取消
              </Button>
              <Button type="submit" variant="primary" disabled={busy || copyName.trim() === ""}>
                复制
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`删除「${pendingDelete ?? ""}」？`}
        description="配置文件会被删除，无法恢复。"
        pending={busy}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        onConfirm={() => void remove()}
      />
    </>
  );
}
