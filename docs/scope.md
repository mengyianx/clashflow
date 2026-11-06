# 功能边界（现状）

一句话：**单容器、单用户、网关自托管**——mihomo 内核 + 中文管理面板 + 两套上游运行面板，管的是一台家用网关上的代理栈，不是通用代理平台。术语以 `GLOSSARY.md` 为准，行为细节以 `README.md` 为准。

## 1. 做什么

### 配置（Config）

- 多配置并存，全局唯一名；服务端派生文件名 `<name>.yaml`，落在 `/data/configs`。
- 两类归属：手写配置（用户内容，永不被刷新改写）与订阅配置（由唯一订阅拥有，每次刷新原样重写）。ADR-0001 决定：面板从不改写既有 YAML，没有覆写 / 合并层。
- 新建 = 空映射 `{}` 或模板（`mixed-port: 7890`、`allow-lan: true`、GEO 自动更新两键）；可从任意配置复制为手写配置。
- 保存 = `mihomo -t` 校验 → 原子替换 → 若为当前配置则热载（`force=true`，不重启内核）。校验失败时旧文件逐字节不变，内核输出原样回显；热载被控制接口拒绝时同样把旧字节写回磁盘（内核继续跑旧配置，下一次重启不会启动被拒绝的配置），控制接口的拒绝原文原样回显（`502`）。
- 切换当前配置走同一条校验 + 热载管线，成功才记录；节点选择按代理组名经内核 `cache.db` 保留，组名不存在则该选择静默丢失。
- 删除：当前配置拒绝（`409`），订阅配置拒绝直删（须删订阅）。

### 订阅（Subscription）

- 只接受 `http(s)` URL，内容必须是**完整 mihomo YAML**（不做 subconverter、Age 解密、节点过滤、多订阅合并）。
- 导入即生成一份订阅配置；刷新只重写它自己，是当前配置时热载。
- 手动刷新 + 定时刷新：全局默认 24 h，可按订阅覆盖间隔；每订阅可自定义 User-Agent（默认 `ClashFlow/0.1`）。
- 失败保留旧配置并在订阅上标记原因与失败类别（刷新管线内的失败分三类：`network` 网络故障 / `kernel-rejected` 内核拒绝 / `hot-apply-refused` 热载被拒；管线外的失败——例如订阅对应的配置记录已经不在——只留原始文本，不带类别），下一轮自动重试，成功即清除。类别只说明"哪里失败"：非法 YAML 由内核判定，归入 `kernel-rejected` 一类，面板不自行解析 YAML。热载被拒时旧配置逐字节不变（与保存共用同一套恢复实现）。

### 服务控制（Service Control）

- 启动 / 停止 / 重启；内核意外退出按指数退避自动拉起（基数 `CLASHFLOW_RESPAWN_BASE_DELAY`，默认 1 s），连续 5 次失败转 `degraded` 并停止自动拉起，手动 Start 恢复。
- 内核版本默认取镜像基线（Image Baseline）；换版本只能走运行时更新（Runtime Update，见 ADR-0003）——面板「系统」页手动安装，不做自动升级。内核自带的 `/restart`、`/upgrade` 不开放，控制接口自己的 `PUT /configs` 也不开放：面板的反代在转发前就拒绝它们（`403`）——前两个按进程原始 `-f` 重启，会与当前配置脱节；后者绕过面板的校验与原子替换，直接改内核在跑的配置路径。
- GEO 自动更新由内核内置调度负责，且**只在配置里写了 `geo-auto-update` 时生效**——订阅内容没写，面板不会替你补。

### 运行面板（Runtime Dashboard）

- 两套上游静态面板并存：metacubexd 在 `/ui/`，zashboard 在 `/zui/`；都从新标签页打开；版本同样默认取镜像基线，换版本走运行时更新（Runtime Update，见 ADR-0003）。
- 免填端点：metacubexd 由面板下发部署配置，zashboard 由入口页注入 `#/setup` 参数，两者都指向面板自己的 `/api` 反代。
- Controller secret 只在服务端注入反代；浏览器侧没有任何凭证，上游面板的"升级内核 / TUN"开关被隐藏。

### 系统与更新

- 手动更新三个上游产物：内核（mihomo）、metacubexd、zashboard。面板"系统"页显示当前版本、来源（镜像 / 已更新）、最近检查到的版本，提供"检查更新 / 更新 / 回滚"。
- 只查 GitHub Releases 的 `latest`；`CLASHFLOW_GITHUB_PROXY` 可加下载/API 前缀。更新包落在 `/data/runtime/`，容器重建后保留；每个组件保留上一版供回滚。
- 内核更新会重启内核（用当前配置），新二进制没起来则自动回滚并重启旧内核；两套运行面板是静态文件热切换。
- 镜像仍是默认基线（Image Baseline）：没有手动更新时，跑的就是镜像基线里的版本。

### 鉴权与访问面

- 单密码（`CLASHFLOW_PASSWORD`，必填，缺失即拒绝启动）+ 服务端会话：cookie `HttpOnly`、`SameSite=Lax`、7 天，登出即吊销；会话存进程内存，面板重启后需重新登录。
- 受保护：`/api/*`（含 WebSocket）、`/ui`、`/zui`。公开：`/healthz`、`POST /api/panel/auth/login`、面板静态外壳（登录页必须能打开）。
- 代理端口 `7890`（mixed）对局域网**免认证**；Controller `9090` 只监听容器内回环，不对外发布。

### 界面

- 管理面板中文单语，浅色默认、可切深色并跟随系统；桌面左侧栏、手机底部标签；总览 / 配置 / 订阅 / 编辑器 / 系统五屏 + 两套运行面板入口。
- YAML 编辑器（CodeMirror 6，行号 + 语法高亮）；成功走 toast，失败走顶部常驻条：中文标题 + 原文（内核输出不翻译、不截断）。
- 两套运行面板保持上游界面与语言（metacubexd 自带 en/zh/ru/ja/ko/fr/fa 并跟随浏览器语言，zashboard 自带 zh/en），面板不改写。

### 交付

- 一条 `docker run` 得到可用代理；数据全在单卷 `/data`（配置、`clashflow.db`、内核 `cache.db`、GEO 与 provider 缓存），备份 = 拷整个卷。
- 镜像由 GitHub Actions 构建：PR 只做三架构验证（不推送），发版是**手动** `workflow_dispatch` 输入版本号，产物 `ghcr.io/<owner>/<repo>:{vX.Y.Z, vX.Y, latest}`（`latest` = 最近一次手动发布的版本，不是 `flow` 的当前提交），多架构 `amd64 / arm64 / arm/v7`（arm/v7 尽力而为）。

## 2. 明确不做

| 不做 | 原因 / 现状 |
|---|---|
| 多用户、角色、审计 | 家用单用户场景；一个密码就是全部身份 |
| TUN / 透明网关 / 网关接管 | v1 只做显式代理端口；zashboard 的 TUN 开关已隐藏 |
| 订阅转换（subconverter）、节点过滤、Age 解密 | 只接受供应商给的完整 YAML，面板不做内容加工 |
| 覆写 / 合并层 | ADR-0001：配置内容原样归属用户，面板不改写 |
| 多订阅合并成一份配置 | 一份订阅恰好一份配置；要合并请在上游供应商侧完成 |
| 流量历史、统计图表、告警通知 | 实时数据只在运行面板里看；面板不落历史 |
| OpenClash 式诊断报告 | 排障靠内核原文与容器日志 |
| 升级 ClashFlow 自身（面板代码） | 面板与内核同镜像交付，换面板仍要拉新镜像重建容器；内核/运行面板见上一节 |
| HTTPS / TLS 终结 | 面板纯 HTTP（见 ADR-0002 的明确折衷）；需要 TLS 请自行在前面放反代 |
| 面板多语言切换 | 中文单语；运行面板的语言归上游管 |
| 多实例 / 集群 / K8s 编排 | 单容器单进程，会话与锁都在进程内 |
| 密码之外的限流、防爆破 | 依赖 LAN 边界；公网暴露不在支持范围 |

## 3. 量化契约

| 项 | 值 |
|---|---|
| 端口 | `7890` 代理（mixed，LAN 免认证）、`8080` 面板（HTTP）、`9090` Controller（仅容器内回环） |
| 必填环境变量 | `CLASHFLOW_PASSWORD` |
| 可选环境变量 | `CLASHFLOW_SECRET`、`CLASHFLOW_SCHEDULER_TICK_SECONDS`（60）、`CLASHFLOW_RESPAWN_BASE_DELAY`（1.0）、`CLASHFLOW_MAX_RESPAWNS`（5）、`CLASHFLOW_DATA_DIR` / `_WEB_DIST` / `_UI_DIST` / `_ZUI_DIST`、`CLASHFLOW_BIND`（0.0.0.0）/ `CLASHFLOW_PORT`（8080）、`CLASHFLOW_KERNEL`（`mihomo`）、`CLASHFLOW_VERSIONS_FILE`（`webui-versions.json`）、`CLASHFLOW_GITHUB_API`（`https://api.github.com`）/ `CLASHFLOW_GITHUB_PROXY`（空） |
| 配置名规则 | 非空、首尾无空白、不等于 `.` / `..`、不含 `/` `\` NUL |
| 订阅抓取 | 超时 30 s、默认 UA `ClashFlow/0.1`、默认间隔 1440 min |
| 会话 | 7 天，进程内存，登出即吊销 |
| 连续崩溃 | 5 次后转 `degraded` |
| 镜像基线（Image Baseline） | mihomo `v1.19.32`、metacubexd `v1.273.1`、zashboard `v3.30.0`，可在系统页手动更新 |
| 更新来源 | GitHub Releases `latest`；`CLASHFLOW_GITHUB_API` 覆盖 API 地址，`CLASHFLOW_GITHUB_PROXY` 加统一前缀 |
| 更新落盘 | `/data/runtime/`（内核与两套面板各保留当前版 + 上一版） |
| 镜像矩阵 | `linux/amd64`、`linux/arm64`、`linux/arm/v7`（尽力而为） |

## 4. 已知取舍

- 内核写出的报错永远是英文（`mihomo -t` 输出、Controller 拒绝原文），面板只加中文标题，不翻译正文。
- `mihomo -t` 有联网副作用：配置引用的 GEO 缺失时会尝试下载，下载失败判校验失败——离线环境保存会失败。
- 面板纯 HTTP，密码与 cookie 可被同网嗅探；这是家用 LAN 的明确折衷，公网暴露不在支持范围。
- 订阅 URL 可能含凭证，与配置一样明文落在 `/data`。
- 升级镜像时若浏览器停留在旧页面，按需加载的编辑器 chunk 会 404，刷新即可。
- 运行时更新后，镜像不再是"跑着什么版本"的唯一真源；只保留上一版回滚，且只能跟 GitHub `latest`，不能钉任意版本。

## 5. 边界怎么改

超出上表的任何能力（TUN、多用户、订阅加工、历史统计、TLS 终结……）都算范围变更：先写 ADR 说明取舍，再改 `GLOSSARY.md` 术语与本文，最后才动代码。
