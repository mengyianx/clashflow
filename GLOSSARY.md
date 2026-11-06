# ClashFlow

ClashFlow is a self-hosted management panel for a mihomo (Clash.Meta) proxy stack on a single personal gateway, shipped as one Docker container. This glossary fixes the domain vocabulary used in issues, code, and docs.

The English term is canonical: issues, code, and docs use it. `_中文_` is the name the Management Panel shows instead — a display mapping, never a second word for the code to use.

## Language

**Kernel**:
The mihomo process that performs all proxying and whose lifecycle ClashFlow supervises.
_中文_: 内核
_Avoid_: core, clash core

**Management Panel**:
The web interface served by ClashFlow itself, covering subscription management, config editing, and service control.
_中文_: 管理面板
_Avoid_: dashboard

**Runtime Dashboard**:
A bundled upstream web interface used for runtime operations against the Kernel API (node selection, delay tests, connection and traffic monitoring); the image ships two, metacubexd under `/ui/` and zashboard under `/zui/`.
_中文_: 运行面板
_Avoid_: dashboard

**Runtime Update**:
A manual move of the Kernel or a Runtime Dashboard to a newer upstream release. Installed versions live in the data volume with one version kept for undo; the image still supplies the baseline.
_中文_: 运行时更新
_Avoid_: upgrade

**Image Baseline**:
The version of the Kernel or a Runtime Dashboard that ships inside the image and runs when no Runtime Update is installed.
_中文_: 镜像基线
_Avoid_: default version

**Subscription**:
One or more remote URLs that, together, yield exactly one Source Config whenever ClashFlow refreshes it.
_中文_: 订阅
_Avoid_: sub, feed

**Subscription Conversion**:
The step that turns what a Subscription returns — share links, a base64 list, or a full YAML — into a complete mihomo YAML.
_中文_: 订阅转换
_Avoid_: sub conversion, link parsing

**Source Config**:
A mihomo YAML owned by the user and edited in the Management Panel; a Subscription refresh rewrites exactly one Source Config verbatim.
_中文_: 源配置
_Avoid_: profile, user config, config

**Runtime Config**:
The mihomo YAML the Kernel actually runs, rendered from one Source Config and the Overlay; never edited by hand.
_中文_: 运行配置
_Avoid_: effective config, generated config, final config

**Overlay**:
The single panel-owned set of settings applied over every Source Config at render time — gateway concerns (TUN Mode, DNS, ports, log level) and the override modules — whose keys win over the Source Config's.
_中文_: 网关覆盖
_Avoid_: override, overwrite layer, merge layer

**Active Config**:
The Source Config whose Runtime Config the Kernel is currently running.
_中文_: 当前配置
_Avoid_: current config

**Hand-written Config**:
A Source Config not backed by any Subscription; its content belongs to the user and is never rewritten by a refresh.
_中文_: 手写配置
_Avoid_: manual config, custom config

**Subscription-derived Config**:
A Source Config owned by exactly one Subscription; every refresh rewrites it verbatim.
_中文_: 订阅配置
_Avoid_: subscription config

**TUN Mode**:
The Overlay state in which the Kernel captures the gateway's LAN traffic through a TUN device instead of only listening on the proxy port.
_中文_: TUN 模式
_Avoid_: transparent proxy, gateway mode, tun2socks

**Failure Class**:
The named category of a failed Subscription refresh — `network`, `conversion-failed`, `kernel-rejected`, `hot-apply-refused` or `render-failed` — carried beside the raw error text so a self-hoster can tell where the refresh failed without reading the Kernel's English output.
_中文_: 失败类别
_Avoid_: error kind, error type

**Controller**:
mihomo's external-controller REST/WebSocket API, the channel for runtime operations and for applying a Runtime Config.
_中文_: 控制接口
_Avoid_: 9090 API

**Service Control**:
ClashFlow's authority over the Kernel process lifecycle, over rendering a Source Config into a Runtime Config, and over applying a Runtime Config to the Kernel.
_中文_: 服务控制
_Avoid_: service management

**Watchdog**:
ClashFlow's periodic probing of the Kernel — process liveness, TUN device and routes, outbound connectivity, DNS resolution — that restarts it after repeated failures.
_中文_: 看门狗
_Avoid_: health check, monitor
