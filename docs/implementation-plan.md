# ClashFlow v1 实现规划

状态：设计共识已确认（见 `GLOSSARY.md` 术语、`docs/adr/0001`、`docs/adr/0002`），**尚未开始实现**。本文是实现前的施工规划，实现启动后按里程碑推进。

## 1. 范围

**v1 做**：

- **订阅管理**：多订阅并存；手动刷新 + 自动更新（全局默认 24h，每订阅可覆盖）；刷新失败保留旧 Config、订阅状态标红并注明原因，下轮调度重试。只接受完整 mihomo YAML 订阅（不做 subconverter、不做 Age 解密、不做节点过滤）。
- **配置管理**：多配置并存（每订阅一份 Config，刷新只重写自己那份、内容原样保留；手编配置独立，永不被刷新触碰）；纯 YAML 编辑器（保存 = 校验 + 热载，错误原样回显）；订阅派生配置允许手改但警示"下次刷新将覆盖"、提供"复制为手编配置"；新建 = 空白模板（`mixed-port: 7890`、`allow-lan: true` 骨架）或复制现有；任一时刻一份 Active Config。
- **服务控制**：启动/停止/重启内核；保存/切换配置先 `mihomo -t` 校验再热载；崩溃自动拉起；GEO 自动更新。内核版本跟随镜像，不做容器内自升级。
- **运行面板**：内嵌 metacubexd 静态站（节点选择、延迟测试、连接、流量、日志全部由它提供）。
- **安全**：面板密码 + session cookie；controller 仅本机 + 后端反代；代理端口 LAN 免认证；面板纯 HTTP——LAN 明文传输是家用场景的明确折衷（密码/cookie 可被同网嗅探；被否决的直连方案暴露的是长期全权 secret，风险性质不同，见 ADR-0002）。

**v1 不做**（明确拒绝）：覆写/合并层 · subconverter · 多用户 · TUN/透明网关 · 监控历史/告警/通知 · OpenClash 式调试诊断报告 · 容器内内核升级。

## 2. 运行形态与契约

```text
container (tini 为 PID 1)
├── clashflow   (FastAPI, :8080)   # 面板 API + 静态前端 + /api/* 反代（含 WebSocket）
└── mihomo      (-d /data -f <active> -ext-ctl 127.0.0.1:9090 -secret <secret>)
```

| 契约 | 值 |
|---|---|
| 代理端口 | `7890`（mixed，`allow-lan: true`，LAN 设备显式代理） |
| 面板 | `8080`（LAN，HTTP） |
| controller | `9090`，仅 `127.0.0.1`，不经 LAN 暴露 |
| `CLASHFLOW_PASSWORD` | 面板密码（必填环境变量） |
| `CLASHFLOW_SECRET` | 可选，覆盖 controller secret；缺省随机生成一次并持久化到 `/data` |

要点（事实支撑，见调研）：`-ext-ctl`/`-secret` 优先级高于 YAML 且 SIGHUP 后仍生效——controller 配置对订阅刷新/手编 YAML 免疫；内核自重启 API（`POST /restart`/`/upgrade`）**不使用**（它按进程原始 `-f` 重启，与 Active Config 可能不一致），重启一律由 ClashFlow 拉起。

## 3. 数据布局（单卷 `/data`，即 mihomo home dir）

```text
/data
├── clashflow.db          # 元数据：订阅(URL/间隔/上次更新/错误状态)、active 记录、secret
├── configs/
│   ├── <订阅名>.yaml      # 订阅派生 Config（刷新原样重写）
│   └── <手编名>.yaml      # Hand-written Config
├── cache.db              # mihomo：节点选择等（profile.store-selected）
├── geoip.metadb / GeoSite.dat / ASN.mmdb
└── providers/            # provider/ruleset 缓存
```

GEO、`cache.db`、订阅、全部 Config 与元数据都落 home dir——不挂卷即回到零配置状态并重下 GEO，`VOLUME /data` 必须声明。Config 名全局唯一，由用户命名并登记在 `clashflow.db`（名称→类型的映射同处）；文件名由服务端按登记名生成，重名拒绝。

## 4. 模块划分

```text
clashflow/                # Python 3.14 + FastAPI
├── main.py               # 装配、静态托管（前端 + /ui/ metacubexd）、生命周期
├── auth.py               # 密码校验、session cookie
├── api/
│   ├── subscriptions.py  # 订阅 CRUD、手动刷新
│   ├── configs.py        # 配置 CRUD、复制、切换 Active
│   └── service.py        # 启停重启、状态、GEO 更新
├── kernel.py             # 进程管理：spawn、守护、指数退避拉起
├── apply.py              # mihomo -t 校验 → 原子替换 → PUT /configs?force=true
├── refresh.py            # 订阅拉取管线
├── scheduler.py          # 自动更新定时
└── apiproxy.py           # /api/* + WS 反代到 127.0.0.1:9090，注入 Authorization

web/                      # React + TS + Vite，产物嵌入后端
├── pages/Subscriptions/  # 列表、状态、手动刷新、间隔设置
├── pages/Configs/        # 列表、激活/复制/新建/删除
├── pages/Editor/         # YAML 编辑器（校验错误回显）
└── pages/Overview/       # 内核状态、/ui/ 运行面板入口
```

## 5. 关键流程

1. **启动**：读 `clashflow.db` → 起 mihomo（`-f` 指向 Active Config 实际路径）→ 轮询 `GET /version` 就绪 → 起 scheduler。内核起不来时面板仍起，服务页红字 + 自动重试。
2. **保存 Config**：写 `<file>.tmp` → `mihomo -d /data -t -f <tmp>` → 通过则 `os.replace` 原子替换 → 若为 Active 则 `PUT /configs {"path": "<file>"}?force=true`；失败则删 tmp、保留旧文件、校验 stderr 原样回前端。
3. **订阅刷新**：GET（自定义 UA、超时）→ 内容必须通过校验（复用流程 2 的 `mihomo -t` tmp 流程，同样受其联网副作用限制）→ 原子替换该订阅的 Config → 是 Active 则热载；失败则旧文件不动、状态标红。
4. **切换 Active**：校验目标 → `PUT /configs {"path": ...}?force=true` → 成功后记录 active；失败则原 Active 不变。已选节点由 `profile.store-selected` 按 proxy-group 名匹配保留——目标 Config 的组名不同或缺失时该选择静默丢失。
5. **崩溃拉起**：kernel 退出 → 指数退避 respawn，`-f` 指向当前 Active Config 实际路径（超限后停 + 红字）；Docker `restart: unless-stopped` 兜底。
6. **生效语义**：热载覆盖 proxies/rules/DNS/端口（force=true 连监听器重建）；controller 类配置从不改（CLI 钉死），故无须重启内核。

注意：`mihomo -t` 有副作用——配置引用的 GEO 缺失时会联网下载，下载失败也判校验失败；校验环境必须能出网。

## 6. 交付

- 基础镜像 `python:3.14-slim` + `tini`；mihomo 二进制按架构从 GitHub releases 下载并 pin 版本（如 v1.19.32），放 `/usr/local/bin/mihomo`。查内核源码须 checkout [`Meta` 分支](https://github.com/MetaCubeX/mihomo/tree/Meta)或对应 `v*` tag——**勿用仓库默认分支**，其为无关项目（核实于 2026-10-18）。
- 镜像矩阵：`linux/amd64`、`linux/arm64`、`linux/arm/v7`（buildx；arm/v7 依网关实际位数保留或裁剪）。
- CI：GitHub Actions（lint/type-check/test + buildx 多架构推送）。
- `VOLUME /data`；`EXPOSE 7890 8080`。

## 7. 里程碑与验收

| 里程碑 | 内容 | 验收（可运行 smoke） |
|---|---|---|
| M1 | 容器骨架、kernel.py、apply.py | `docker run` 起容器；`curl :8080/api/service/health` 200；改配置热载生效 |
| M2 | 配置管理 + 服务控制 API + YAML 编辑器 | API 建/改/复制/切换配置；坏配置被拒且错误原样回显 |
| M3 | 订阅管理 + 自动更新 | 导入订阅生成 Config；刷新失败标红保留旧配置；定时触发可见 |
| M4 | metacubexd 集成 + `/api` 反代 | 浏览器 `/ui/` 连上运行面板（相对路径 WS 若实测不通，以构建期注入绝对反代地址兜底，以连通为准）；切节点/流量/日志全走反代（无直连 9090） |
| M5 | 鉴权 + 多架构 CI + README 部署指南 | 无密码访问被拒；amd64+arm64 镜像产出（arm/v7 尽力而为，裁剪须在 PR 说明理由）；按 README 从零部署成功 |

## 8. 风险与待定项

1. **metacubexd 的 URL/WS 行为需 M4 实测**：`config.js` 预置反代地址可行（其深链/探测机制已查明），但相对路径在 WebSocket 场景的表现要以实测为准。
2. **`mihomo -t` 联网副作用**（见 §5）：离线或 GEO 源不可达时校验会失败，v1 接受此限制。
3. **arm/v7 依赖可用性**：Python 3.14 及依赖在 armv7 的轮子可能缺失，不行则矩阵裁为 amd64+arm64。
4. **metacubexd 版本策略**：倾向 release tarball 固定版本（可复现），不跟 gh-pages 流动。
5. **订阅 URL 含凭证**，与配置同落 `/data`；单用户家用场景接受。
