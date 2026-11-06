import { ApiError } from "./api";
import type {
  ConfigKind,
  KernelState,
  SubscriptionFailureClass,
  SubscriptionState,
} from "./api";

/** UI labels for the enums the API returns (the wire values stay English). */
export const KERNEL_STATE_LABEL: Record<KernelState, string> = {
  up: "运行中",
  down: "已停止",
  respawning: "重启中",
  degraded: "已停用（连续崩溃）",
};

export const SUBSCRIPTION_STATE_LABEL: Record<SubscriptionState, string> = {
  ok: "正常",
  error: "失败",
  pending: "待首次刷新",
};

export const CONFIG_KIND_LABEL: Record<ConfigKind, string> = {
  "hand-written": "手写配置",
  "subscription-derived": "订阅配置",
};

/** The class of a failed Subscription refresh (ticket #27): the Chinese name
 * of the class, shown above the raw error text. The wire values stay
 * English; the raw text below is never translated. */
export const SUBSCRIPTION_FAILURE_CLASS_LABEL: Record<SubscriptionFailureClass, string> = {
  network: "网络故障",
  "render-failed": "渲染失败",
  "kernel-rejected": "内核拒绝",
  "hot-apply-refused": "热载被拒",
};

/** Panel messages carry a Chinese title; the original text is always kept.
 * Prefixes are the API's own wording, locked by the Python tests, so a backend
 * rename breaks loudly instead of silently degrading to "操作失败". */
const KNOWN_MESSAGES: Array<[prefix: string, title: string]> = [
  ["subscription fetch failed", "订阅抓取失败"],
  ["config name must not be empty or whitespace-padded", "配置名称不能为空或首尾带空白"],
  ["config name contains characters unsafe in a filename", "配置名称含文件名不允许的字符"],
  ["config name already registered", "配置名称已被占用"],
  ["no such config", "配置不存在"],
  ["config is active; activate another config before deleting", "该配置正在运行，不能删除"],
  ["subscription already registered", "订阅名称已被占用"],
  ["no such subscription", "订阅不存在"],
  ["subscription has no source config", "订阅没有可用的源配置"],
  ["config file already exists", "同名配置文件已存在"],
  ["config file missing", "配置文件缺失"],
  ["config is the source of subscription", "该配置由订阅持有，请删除对应订阅"],
  ["subscription URL must be http(s)", "订阅地址必须是 http(s)"],
  ["user agent must not be empty", "User-Agent 不能为空"],
  ["user agent must not contain control characters", "User-Agent 含控制字符"],
  ["update interval must be a positive number of minutes", "更新间隔必须是不小于 1 的整数分钟"],
  ["kernel unreachable", "内核没有响应"],
  // The refused Controller endpoints are not listed here: they answer 403,
  // and STATUS_TITLES[403] is their one Chinese title.
  ["release check failed", "检查更新失败"],
  ["download failed", "下载失败"],
  ["bundle has no index.html", "更新包不含入口页面"],
  ["bundle is missing asset", "更新包缺少静态资源"],
  ["bundle references no script", "更新包不是完整面板"],
  ["archive could not be extracted", "更新包无法解压"],
  ["kernel update rolled back", "内核更新失败，已回滚"],
  ["the new kernel did not become ready", "新内核没有启动成功"],
  ["no previous version to roll back to", "没有可回滚的版本"],
  ["the previous version is no longer on disk", "上一版文件已不在磁盘上"],
  ["an update is already running", "已有更新正在进行"],
];

/** A render pipeline failure names its stage, and an Overlay rejection names
 * the offending key; both are content errors the user can act on. */
const RENDER_FAILURE = /^(key-merge|rule-injection|domain-sniff|objects): /;
const OVERLAY_FAILURE = /^overlay: /;

const STATUS_TITLES: Record<number, string> = {
  0: "面板连接中断",
  400: "请求无效",
  401: "未登录",
  403: "接口未开放",
  404: "目标不存在",
  405: "只读接口",
  409: "操作被拒绝",
  500: "面板出错",
  502: "上游请求失败",
};

/** The Kernel writes its config-test output to stdout; it is never translated. */
const KERNEL_OUTPUT = /^(level=|time=|configuration file |unsupport |yaml: )|test failed/;

export interface MessageText {
  title: string;
  detail: string;
}

function describeMessage(raw: string): MessageText {
  const detail = raw.trim();
  for (const [prefix, title] of KNOWN_MESSAGES) {
    if (detail.startsWith(prefix)) return { title, detail };
  }
  if (RENDER_FAILURE.test(detail)) return { title: "渲染失败", detail };
  if (OVERLAY_FAILURE.test(detail)) return { title: "网关覆盖无效", detail };
  if (KERNEL_OUTPUT.test(detail)) return { title: "内核校验未通过", detail };
  return { title: "操作失败", detail };
}

export function describeError(error: unknown): MessageText {
  if (error instanceof ApiError) {
    if (error.status === 0) {
      return { title: "面板连接中断", detail: "无法连接面板，请确认容器仍在运行。" };
    }
    const described = describeMessage(error.detail);
    if (described.title !== "操作失败") return described;
    return {
      title: STATUS_TITLES[error.status] ?? `请求失败（HTTP ${error.status}）`,
      detail: error.detail,
    };
  }
  return { title: "操作失败", detail: error instanceof Error ? error.message : String(error) };
}
