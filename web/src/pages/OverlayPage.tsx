import { Download, Save, Upload } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
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
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

/** The M1 Overlay surface: one global record applied over every Source
 * Config. A field left blank is NOT owned, so each Source Config keeps its
 * own value for that key; only filled fields are sent. Keys without a control
 * here (the TUN block, DNS mode) stay in the schema for 2.0 M2. */

const LOG_LEVELS = ["silent", "error", "warning", "info", "debug"];

interface OverlayForm {
  logLevel: string;
  mixedPort: string;
  allowLan: string;
  bindAddress: string;
  nameservers: string;
  fallbacks: string;
}

const EMPTY_FORM: OverlayForm = {
  logLevel: "",
  mixedPort: "",
  allowLan: "",
  bindAddress: "",
  nameservers: "",
  fallbacks: "",
};

const EDITABLE_TOP_LEVEL: Record<string, true> = {
  "log-level": true,
  "mixed-port": true,
  "allow-lan": true,
  "bind-address": true,
  dns: true,
};

function parseLines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

function formFromValues(values: Record<string, unknown>): OverlayForm {
  const dns = (typeof values.dns === "object" && values.dns !== null ? values.dns : {}) as Record<
    string,
    unknown
  >;
  const level = values["log-level"];
  const port = values["mixed-port"];
  const allowLan = values["allow-lan"];
  const bindAddress = values["bind-address"];
  return {
    logLevel: typeof level === "string" ? level : "",
    mixedPort: typeof port === "number" ? String(port) : "",
    allowLan: typeof allowLan === "boolean" ? String(allowLan) : "",
    bindAddress: typeof bindAddress === "string" ? bindAddress : "",
    nameservers: Array.isArray(dns.nameserver) ? (dns.nameserver as string[]).join("\n") : "",
    fallbacks: Array.isArray(dns.fallback) ? (dns.fallback as string[]).join("\n") : "",
  };
}

/** Build the replace-state for the API: the editable fields plus every key
 * this page has no control for, preserved verbatim (a TUN block imported from
 * YAML must not disappear just because the M1 form saved). */
function valuesFromForm(original: Record<string, unknown>, form: OverlayForm): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(original)) {
    if (!EDITABLE_TOP_LEVEL[key]) values[key] = value;
  }
  if (form.logLevel) values["log-level"] = form.logLevel;
  if (form.mixedPort.trim() !== "") values["mixed-port"] = Number(form.mixedPort);
  if (form.allowLan !== "") values["allow-lan"] = form.allowLan === "true";
  if (form.bindAddress.trim() !== "") values["bind-address"] = form.bindAddress.trim();

  const originalDns = (
    typeof original.dns === "object" && original.dns !== null ? original.dns : {}
  ) as Record<string, unknown>;
  const dns: Record<string, unknown> = { ...originalDns };
  delete dns.nameserver;
  delete dns.fallback;
  const nameservers = parseLines(form.nameservers);
  if (nameservers.length > 0) dns.nameserver = nameservers;
  const fallbacks = parseLines(form.fallbacks);
  if (fallbacks.length > 0) dns.fallback = fallbacks;
  if (Object.keys(dns).length > 0) values.dns = dns;
  return values;
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="grid gap-1.5 py-3.5 md:grid-cols-[190px_1fr] md:items-start md:gap-4">
      <div>
        <div className="text-sm font-medium text-ink">{label}</div>
        {hint ? <div className="mt-0.5 text-xs text-ink-3">{hint}</div> : null}
      </div>
      <div>{children}</div>
    </div>
  );
}

const controlClass =
  "w-full rounded-[var(--radius-control)] border border-line bg-surface px-2.5 py-1.5 text-[13px] text-ink focus:border-accent focus:ring-4 focus:ring-[var(--tint)] focus:outline-none";

export function OverlayPage() {
  const { banner, show, showText, clear } = useErrorBanner();
  const [values, setValues] = useState<Record<string, unknown> | null>(null);
  const [form, setForm] = useState<OverlayForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [clearOpen, setClearOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const state = await api.overlay.get();
      setValues(state.values);
      setForm(formFromValues(state.values));
    } catch (error) {
      show(error);
    }
  }, [show]);

  useEffect(() => {
    void load();
  }, [load]);

  const update = (patch: Partial<OverlayForm>) => setForm((current) => ({ ...current, ...patch }));

  const save = async () => {
    if (form.mixedPort.trim() !== "" && !/^\d+$/.test(form.mixedPort.trim())) {
      showText("端口格式不正确", "mixed-port 必须是 1-65535 的整数。");
      return;
    }
    setBusy(true);
    clear();
    try {
      const next = await api.overlay.save(valuesFromForm(values ?? {}, form));
      setValues(next.values);
      setForm(formFromValues(next.values));
      toast.success("已保存网关覆盖，当前配置已重新渲染");
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  const clearOverrides = async () => {
    setBusy(true);
    try {
      const next = await api.overlay.save({});
      setValues(next.values);
      setForm(EMPTY_FORM);
      setClearOpen(false);
      toast.success("已清空网关覆盖：源配置的值重新生效");
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  const importYaml = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const next = await api.overlay.import(importText);
      setValues(next.values);
      setForm(formFromValues(next.values));
      setImportOpen(false);
      setImportText("");
      toast.success("已导入网关覆盖");
    } catch (error) {
      show(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="mb-5">
        <h1 className="font-serif text-[30px] leading-tight font-semibold">网关覆盖</h1>
        <p className="mt-1.5 text-sm text-ink-2">
          全局一份，对所有源配置生效。留空的项不被接管，沿用各配置自己的值；保存后当前配置会重新渲染并热载。
        </p>
      </div>

      <ErrorBanner banner={banner} onDismiss={clear} />

      <Card className="mb-4 px-4 py-1 md:px-5">
        <Field label="日志级别" hint="log-level，改完即刻热载">
          <select
            className={controlClass}
            value={form.logLevel}
            onChange={(event) => update({ logLevel: event.target.value })}
          >
            <option value="">未覆盖（沿用源配置）</option>
            {LOG_LEVELS.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </Field>

        <Field label="混合代理端口" hint="mixed-port">
          <Input
            inputMode="numeric"
            placeholder="未覆盖，例如 7890"
            value={form.mixedPort}
            onChange={(event) => update({ mixedPort: event.target.value })}
          />
        </Field>

        <Field label="允许局域网" hint="allow-lan">
          <select
            className={controlClass}
            value={form.allowLan}
            onChange={(event) => update({ allowLan: event.target.value })}
          >
            <option value="">未覆盖（沿用源配置）</option>
            <option value="true">允许</option>
            <option value="false">禁止</option>
          </select>
        </Field>

        <Field label="监听地址" hint="bind-address，* 表示全部地址">
          <Input
            placeholder="未覆盖，例如 * 或 0.0.0.0"
            value={form.bindAddress}
            onChange={(event) => update({ bindAddress: event.target.value })}
          />
        </Field>

        <Field label="DNS 上游" hint="dns.nameserver，每行一个">
          <textarea
            className={controlClass}
            rows={3}
            placeholder={"未覆盖，例如\n1.1.1.1\nhttps://dns.example/dns-query"}
            value={form.nameservers}
            onChange={(event) => update({ nameservers: event.target.value })}
          />
        </Field>

        <Field label="DNS 兜底" hint="dns.fallback，每行一个">
          <textarea
            className={controlClass}
            rows={3}
            placeholder="未覆盖"
            value={form.fallbacks}
            onChange={(event) => update({ fallbacks: event.target.value })}
          />
        </Field>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => void save()} disabled={busy || values === null}>
          <Save />
          保存并热载
        </Button>
        <Button asChild disabled={values === null}>
          <a href={api.overlay.exportPath} download="overlay.yaml">
            <Download />
            导出 YAML
          </a>
        </Button>
        <Button onClick={() => setImportOpen(true)} disabled={busy || values === null}>
          <Upload />
          导入 YAML
        </Button>
        <Button
          variant="ghost"
          className="ml-auto"
          onClick={() => setClearOpen(true)}
          disabled={busy || values === null || Object.keys(values ?? {}).length === 0}
        >
          清空全部覆盖
        </Button>
        <Badge variant="mute">{Object.keys(values ?? {}).length} 组键已接管</Badge>
      </div>

      <p className="mt-3 text-xs text-ink-3">
        TUN、DNS 模式与完整端口族在 2.0 数据面版本开放；本版只接管上面几项，其余键暂不接受导入。
      </p>

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent>
          <DialogTitle>导入网关覆盖 YAML</DialogTitle>
          <DialogDescription>
            导入是整体替换，不合并。导出的文件可以直接粘回来。
          </DialogDescription>
          <form onSubmit={(event) => void importYaml(event)}>
            <textarea
              className={`${controlClass} mt-3 font-mono`}
              rows={10}
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              placeholder={"log-level: info\ndns:\n  nameserver:\n    - 1.1.1.1"}
            />
            <DialogFooter>
              <Button type="button" onClick={() => setImportOpen(false)}>
                取消
              </Button>
              <Button
                type="submit"
                variant="primary"
                disabled={busy || importText.trim() === ""}
              >
                导入并应用
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={clearOpen}
        title="清空全部网关覆盖？"
        description="所有源配置将不再被覆盖，当前配置会按源内容重新渲染并热载。"
        confirmLabel="清空"
        pending={busy}
        onOpenChange={setClearOpen}
        onConfirm={() => void clearOverrides()}
      />
    </>
  );
}
