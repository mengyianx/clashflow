import { Eye, RotateCcw, Save } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { ErrorBanner, useErrorBanner } from "@/components/ErrorBanner";
import { YamlEditor } from "@/components/YamlEditor";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { api, type ConfigContent } from "@/lib/api";
import { CONFIG_KIND_LABEL } from "@/lib/messages";

export function EditorPage() {
  const { name = "" } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const { banner, show, clear } = useErrorBanner();
  const [entry, setEntry] = useState<ConfigContent | null>(null);
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const [copyName, setCopyName] = useState("");

  const load = useCallback(async () => {
    if (!name) return;
    try {
      const next = await api.configs.get(name);
      setEntry(next);
      setContent(next.content);
      setSavedContent(next.content);
    } catch (error) {
      show(error);
    }
  }, [name, show]);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = content !== savedContent;

  const save = async () => {
    setBusy(true);
    clear();
    try {
      await api.configs.save(name, content);
      setSavedContent(content);
      toast.success(entry?.active ? "已保存并热载" : "已保存");
      await load();
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  const copy = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.configs.copy(name, copyName);
      toast.success(`已复制为 ${copyName}`);
      setCopyOpen(false);
      setCopyName("");
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="mb-5">
        <h1 className="font-serif text-[30px] leading-tight font-semibold">{name}</h1>
        <p className="mt-1.5 text-sm text-ink-2">
          {entry
            ? `${CONFIG_KIND_LABEL[entry.kind]} · ${entry.filename} · ${entry.active ? "当前配置" : "未在运行"}`
            : "正在读取…"}
        </p>
        {entry ? (
          <p className="mt-1 text-xs text-ink-3">
            {entry.summary
              ? `内容：${entry.summary.nodes} 节点 / ${entry.summary.groups} 组 / ${entry.summary.rules} 规则`
              : "内容：尚未通过校验"}
          </p>
        ) : null}
      </div>

      <ErrorBanner banner={banner} onDismiss={clear} />

      {entry?.summary?.age_key_missing ? (
        <Alert variant="warn" title="缺少 Age key" className="mb-4">
          这份配置是 Age 加密的，面板没有可用 key，无法统计内容。保存会在渲染阶段被拒。
        </Alert>
      ) : null}

      {entry && entry.overridden_keys.length > 0 ? (
        <Alert variant="info" title="以下键由网关覆盖改写" className="mb-4">
          <div>
            保存后内核运行的是网关覆盖的值，这里的改动对这些键不会生效：
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {entry.overridden_keys.map((key) => (
              <Badge key={key} variant="warn">
                {key}
              </Badge>
            ))}
          </div>
          <Button
            size="sm"
            className="mt-2.5"
            onClick={() => navigate(`/configs/${encodeURIComponent(name)}/runtime`)}
          >
            <Eye />
            查看运行配置与差异
          </Button>
        </Alert>
      ) : null}

      {entry?.kind === "subscription-derived" ? (
        <Alert
          variant="warn"
          title="这是一份订阅配置。"
          className="mb-4"
        >
          保存可以生效，但下次订阅刷新会用它自己的内容覆盖这里的修改。
          <Button
            size="sm"
            className="ml-2 align-middle"
            onClick={() => {
              setCopyName(`${entry.name} 副本`);
              setCopyOpen(true);
            }}
          >
            复制为手写配置
          </Button>
        </Alert>
      ) : null}

      <div className="overflow-hidden rounded-[var(--radius-card)] border border-line-soft bg-surface shadow-[var(--shadow-card)]">
        <div className="flex items-center gap-2.5 border-b border-line-soft px-4 py-3">
          <Badge>YAML</Badge>
          <span className="hidden text-xs text-ink-3 md:inline">
            保存 = 内核校验 + 原子替换 + 热载
          </span>
          <div className="ml-auto flex gap-2">
            <Button
              size="sm"
              onClick={() => navigate(`/configs/${encodeURIComponent(name)}/runtime`)}
              disabled={busy}
            >
              <Eye />
              运行配置
            </Button>
            <Button size="sm" onClick={() => void load()} disabled={busy}>
              <RotateCcw />
              重新加载
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={() => void save()}
              disabled={busy || !dirty}
            >
              <Save />
              保存
            </Button>
          </div>
        </div>
        <div className="h-[calc(100vh-330px)] min-h-[320px]">
          <YamlEditor value={content} onChange={setContent} />
        </div>
        <div className="flex items-center gap-2 border-t border-line-soft px-4 py-3">
          <span className="text-xs text-ink-3">保存前由内核校验，失败时旧文件保持不变</span>
          <Badge variant={dirty ? "warn" : "mute"} className="ml-auto">
            {dirty ? "有未保存的修改" : "已保存"}
          </Badge>
        </div>
      </div>

      <Dialog open={copyOpen} onOpenChange={setCopyOpen}>
        <DialogContent>
          <DialogTitle>复制为手写配置</DialogTitle>
          <DialogDescription>
            副本与订阅脱钩：刷新不会再改写它，你可以对它随意编辑。
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
              <Button type="button" onClick={() => setCopyOpen(false)}>
                取消
              </Button>
              <Button type="submit" variant="primary" disabled={busy || copyName.trim() === ""}>
                复制
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
