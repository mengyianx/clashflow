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
The bundled metacubexd web interface used for runtime operations against the Kernel API (node selection, delay tests, connection and traffic monitoring).
_中文_: 运行面板
_Avoid_: dashboard

**Subscription**:
A remote URL that yields a complete mihomo YAML Config; ClashFlow fetches it to refresh exactly one Config.
_中文_: 订阅
_Avoid_: sub, feed

**Config**:
A mihomo YAML configuration file managed by ClashFlow; the active Config determines the Kernel's behavior.
_中文_: 配置
_Avoid_: profile

**Active Config**:
The single Config the Kernel is currently running.
_中文_: 当前配置
_Avoid_: current config

**Hand-written Config**:
A Config not backed by any Subscription; its content belongs to the user and is never rewritten by a refresh.
_中文_: 手写配置
_Avoid_: manual config, custom config

**Subscription-derived Config**:
A Config owned by exactly one Subscription; every refresh rewrites it verbatim.
_中文_: 订阅配置
_Avoid_: subscription config

**Controller**:
mihomo's external-controller REST/WebSocket API, the channel for runtime operations and for applying a Config.
_中文_: 控制接口
_Avoid_: 9090 API

**Service Control**:
ClashFlow's authority over the Kernel process lifecycle and over applying a Config to it.
_中文_: 服务控制
_Avoid_: service management
