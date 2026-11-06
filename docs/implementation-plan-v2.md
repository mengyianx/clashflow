# ClashFlow v2 实现规划

状态：规划中。决策见 ADR-0004 … 0010，代码未动；实际行为仍以 `README.md` 与 `docs/scope.md`（V1 现状）为准。

目标：**完全取代**一台家用 OpenWrt 上的 OpenClash（旁路由拓扑）。按可验证的节奏拆两版：2.0 能接管日常流量，2.1 对齐管理面。

## 1. 交付切分

| 模块 | 2.0 | 2.1 |
|---|---|---|
| 源配置 / 运行配置 / Overlay 渲染层（ADR-0004、0009） | ✅ | |
| 四段式生成骨架（键合并 → 规则注入 → sniffer/hosts/命名策略 → 组与节点增删改） | ✅ | |
| 渲染健壮性：YAML 容错、规则三锚点、目标组存在性校验、IP 补 CIDR、空组兜底、重名去重、改名联动 | ✅ | |
| 只支持 host 网络 + TUN + 53 强制接管（ADR-0005） | ✅ | |
| 数据面：DNS 模式（fake-ip / redir-host）× TUN 开关 × 端口监听 = 四组合，另有 `rule/global/direct`、全端口族、UDP/QUIC 开关 | ✅ | |
| 应用策略：模式切换重启内核、其余热载、5 分钟 10 次限流、apply 后三步自验（ADR-0010） | ✅ | |
| 旁路由与流量控制：路由器自身代理、绕过大陆、常见端口模式、内网直连、接口绑定、旁路由兼容、masq cache | ✅ | |
| 订阅转换（内置 subconverter）+ 多 URL + ETag + UA 降级 + 默认排除关键字 | ✅ | |
| 订阅参数面：模板、关键词/排除/广告过滤、emoji/排序/节点类型、自定义头（与后端头隔离）、流量与到期 | ✅ | |
| 订阅参数面：Age（内核 key，`mihomo age keygen/convert`）、在线转换后端兜底 | ✅ | |
| 校验三重：`mihomo -t` + YAML 可解析 + 必须含 `proxies`/`proxy-providers` | ✅ | |
| DNS 最小集（listen / nameserver / fallback / fake-ip-range）+ 替换语义 | ✅ | |
| DNS 全量：6 组服务器、fake-ip/fallback 过滤、hosts、域名策略、respect rules、追加系统/WAN DNS、缓存与 AAAA、store-fakeip | | ✅ |
| 设备级排除：IP/CIDR → 内核规则集（ADR-0008） | | ✅ |
| Overlay 键组扩到 OpenClash 全量 | | ✅ |
| 策略组/提供者管理：按源配置作用域 + 一键应用到全部、增量合并保存、smart/policy_filter/health-check | | ✅ |
| 自定义节点：常用 8 种协议逐字段，其余走分享链接导入 + Other Parameters 透传 | | ✅ |
| 节点选择历史：按配置快照与还原 | | ✅ |
| 面板动作：批量删除、Read/Apply、日志三 tab、备份与恢复、删除内核 | | ✅ |
| 内核多版本（保留 3 版） | | ✅ |
| 看门狗：进程/TUN/出网/DNS + 出口 IP 解析 + 分级恢复（先重载配置，再重启内核） | | ✅ |
| 统计、诊断 zip、定时任务统一 | | ✅ |
| 运维组：定时重启、日志上限、Rules/GEO/Chnroute 更新、重载限流之外的单次更新 | | ✅ |
| 导入 OpenClash 最终 `config.yaml` | ✅ | |
| 破坏性迁移与版本门禁（ADR-0006） | ✅ | |

明确不抄：流媒体解锁检测与自动选节点、oixCloud 签到、MAC 级与端口级黑白名单、Small Flash Memory、Delay Start On Boot、UPnP、Smart/LightGBM 组、新手引导遮罩、Dnsmasq Fix（我们没有 dnsmasq）、自定义防火墙脚本入口。仍不做：多用户、多语言、TLS 终结、面板自更新、多实例、多核心、bridge 网络、Docker Desktop。

## 2. 运行形态

```text
host 网络（唯一形态）
├── clashflow    (FastAPI, :8080)                # 面板 + /api/* 反代
├── mihomo       (-d /data -f /data/runtime-configs/<active>.yaml
│                 -ext-ctl 127.0.0.1:9090 -secret <secret>)
└── subconverter (127.0.0.1:<随机端口>，仅面板调用)
```

`docker run` 模板：`--network host --cap-add NET_ADMIN --device /dev/net/tun -v clashflow:/data -e CLASHFLOW_PASSWORD=…`。

## 3. 数据布局

```text
/data
├── clashflow.db          # 元数据 + overlay + stats（PRAGMA user_version）
├── configs/<name>.yaml   # 源配置（用户内容）
├── runtime-configs/      # 运行配置（可重建，内核 -f 指向这里）
├── rulesets/             # 面板生成的规则集：设备排除、绕过大陆
├── history/              # 每份配置的节点选择快照（cache.db 副本）
├── runtime/              # Runtime Update 安装目录（ADR-0003，保持原样）
├── converters/           # subconverter 的配置与缓存
├── logs/                 # 面板/内核/调试日志（带大小上限）
├── backup/v<from>-<ts>/  # 迁移前自动备份（不自动清理）
└── cache.db / geoip.metadb / GeoSite.dat / ASN.mmdb / providers/
```

`/data/rulesets/` 与 `/data/history/` 是新增持久化产物，进备份与诊断包；`runtime/` 与 `runtime-configs/` 只差一个后缀，代码与清理逻辑不能互认。

## 4. 模块增量

```text
clashflow/
├── render.py      # 四段管线；按段报错
├── yamlfix.py     # 容错：首行 base64、protocol-param/short-id 引号、!str 转义
├── rules.py       # 规则注入三锚点、目标组校验、IP 补 CIDR、空组兜底、去重、改名联动
├── summary.py     # 配置摘要：节点/组/规则计数、Age 缺 key
├── overlay.py     # Overlay 读写、YAML 导出/导入、被覆盖键标注、替换/追加语义
├── datapath.py    # 操作模式 × 端口族 × UDP/QUIC 的键映射（六种组合）
├── traffic.py     # 流量控制与旁路由参数
├── acl.py         # 设备排除：IP/CIDR → /data/rulesets + 注入 rule-providers（ipcidr）+ 引用到 tun.route-exclude-address-set
├── subscribe.py   # 多 URL 抓取（ETag、UA 降级、拆分归一化）→ 转换 → 三重校验 → 源配置
├── converter.py   # subconverter 守护、模板与过滤参数、请求头隔离、/version 探测
├── dnsconf.py     # DNS 全量：6 组服务器、过滤器、hosts、域名策略（2.1）
├── nodes.py       # 自定义节点表与分享链接导入（8 种协议 + 透传）（2.1）
├── history.py     # 节点选择快照与还原（2.1）
├── selfcheck.py   # 8080/9090/(TUN 时的)53 拒启；7890/ip_forward/nftables 警告
├── providers.py   # 代理/规则提供者表单与文件、增量合并（2.1）
├── watchdog.py    # 进程/TUN/出网/DNS/出口 IP；分级恢复（2.1）
├── stats.py       # 60 s 采样，按组聚合，7 天清理（2.1）
├── diagnose.py    # 诊断 zip（含规则集、历史、宿主网络快照）（2.1）
├── debug.py       # DNS 调试、连接调试、网络详情（2.1）
├── db.py          # user_version 迁移链、显式关联、stats 表
├── apply.py       # render → 校验三重 → 原子写 → 热载/重启 → 三步自验/回滚
├── kernel.py      # -f 指向运行配置；启动期 TUN 就绪检查
└── updates.py     # 保留最近 3 版；Rules/GEO/Chnroute 更新（2.1）
web/src/pages/
├── Gateway/       # 数据面模式、端口族、流量控制、TUN/DNS 最小集、导入导出
├── Runtime/       # 运行配置只读预览 + 与源配置 diff
├── Subscriptions/ # 多 URL、模板、过滤、流量与到期
├── DNS/           # 6 组服务器与过滤器（2.1）
├── Providers/     # 提供者、策略组、自定义节点（2.1）
├── Stats/         # 总量与按组曲线（2.1）
└── Diagnostics/   # 诊断导出、日志三 tab、调试页（2.1）
```

界面保留 V1 的五屏（总览 / 配置 / 订阅 / 编辑器 / 系统），新增网关、DNS、提供者、统计、诊断五页；总览补运行状态、出口 IP 与配置摘要；不按 OpenClash 的菜单重排。

## 5. 关键流程

1. **启动**：读 `user_version` → 需要则备份并迁移 → 自检端口/环境 → 起 subconverter → 渲染 Active（含数据面与流量控制键、注册排除规则集）→ spawn 内核 → 探活 → **TUN 就绪检查（设备与路由，超时重试，最终 degraded）** → 起调度、采样与看门狗。
2. **保存源配置**：候选源 → `yamlfix` → 四段 `render` → 校验三重 → 原子写源 → 原子写运行配置 → 是 Active 则热载 → **三步自验**；任一步失败，源与运行配置都逐字节保持旧值。
3. **改 Overlay**：校验 → 判断是否触及模式类键（TUN 开关、DNS 增强模式）→ 是则重启内核，否则热载（受 5 分钟 10 次限流约束）；需要时先落规则集文件并注入 provider 条目。渲染期强制约束：`route-exclude-address-set` 非空时 `auto-route` 与 `auto-redirect` 必须为真，否则拒绝保存（关闭时排除集会静默失效）。
4. **订阅刷新**：多 URL 拆分归一化 → 带 ETag 抓取（UA 降级链）→ 形态判定（完整 YAML pass-through；链接/base64 走 subconverter，套模板与过滤，请求头与后端头隔离）→ 三重校验 → 四段 render → 原子写源 → 原子写运行配置 → 是 Active 则热载。失败分类沿用 `network` / `conversion-failed` / `render-failed` / `kernel-rejected` / `hot-apply-refused`。
5. **导入 OpenClash**：上传最终 `config.yaml` → 作为新源配置落盘 → 四段渲染 → 校验三重；订阅与覆写由用户重建（不逆向 uci）。
6. **切换 Active**：保存当前配置的节点选择快照 → 渲染目标源 → 校验 → 写运行配置 → 热载/重启 → 还原目标配置的快照 → 成功后记录。
7. **看门狗**：30 s 检查 tun 设备、默认路由、出网与 DNS 解析、出口 IP；**分级恢复**——先按 config 重载重建路由与规则，连续失败再重启内核一次，仍失败转 `degraded`。出口 IP 解析结果按上游做法写入排除集（不是 `/etc/hosts`）。
8. **统计**：60 s 读一次 `/traffic` 与 `/connections`，聚合成「总量 + 按代理组」入 `stats` 表；启动与每日各清理超过 7 天的行。
9. **诊断**：打包版本、overlay、源与运行配置及 diff、规则集、节点历史、最近日志（面板/内核/调试）、失败类别、宿主网络只读快照（`ip -j addr`、`ip -j route`、`nft list ruleset`、53 监听者）。
10. **配置摘要**：每次校验通过后统计节点数、组数、规则数、Age 是否缺 key，写进配置记录，总览与订阅列表直接展示。

## 6. 里程碑与验收

### 2.0

| 里程碑 | 内容 | 验收（可运行 smoke） |
|---|---|---|
| M1 渲染层与迁移 | `render.py` 四段骨架、`yamlfix.py`、`rules.py`、`summary.py`、迁移链、apply 管线改造；Overlay 页、diff 页、总览状态与摘要 | 用 v1 数据卷启动：自动备份并迁移、内核以运行配置起来；改 `log-level` 后热载且源配置逐字节不变；坏配置被拒时错误带段名、摘要不更新；缺 `proxies` 与 `proxy-providers` 的订阅被三重校验拦下 |
| M2 数据面与旁路由 | DNS 模式 × TUN × 端口四组合、代理模式、全端口族、UDP/QUIC；流量控制全套；模式切换重启、热载限流、apply 自验、启动期 TUN 就绪；netns 端到端用例进 CI | 四组合各起一次，每种都能让一台 LAN 设备出网；切模式后面板提示断连且内核确实重启；手工删掉 tun 设备后自验报 `hot-apply-refused` 并回滚；把 `route-exclude-address-set` 指向不存在的 provider（netns 用例）时自验能抓到 TUN 未创建；`auto-route/auto-redirect` 关闭且排除集非空时保存被拒；切到「绕过大陆」后国内直连、国外走代理 |
| M3 订阅转换与参数面 | subconverter 守护、多 URL 归一化、ETag、UA 降级、模板、过滤、默认排除、emoji/排序/节点类型、自定义头隔离、流量与到期、Age | 导入分享链接订阅 → 生成源配置并可激活；加第二个 URL 后节点合并；套 ACL4SSR 模板后规则组出现；关键词过滤命中且组内引用被摘除；`mihomo age keygen` 后能导入加密订阅；subconverter 被杀 → 标 `conversion-failed`、旧配置不变 |
| M4 导入与发布 2.0 | OpenClash `config.yaml` 导入、迁移说明、README 重写、多架构 | 用导出的 OpenClash 配置导入后整网可用；按新 README 从零部署成功 |

### 2.1

| 里程碑 | 内容 | 验收（可运行 smoke） |
|---|---|---|
| M5 覆写全量与 DNS | Overlay 全量键组；6 组 DNS 服务器、过滤器、hosts、域名策略、respect rules、追加系统/WAN DNS、缓存与 AAAA | 每类键组改一项后 diff 只出现该键；两组 DNS 服务器分流可验证（`dig` 走不同上游）；fake-ip 过滤器命中与不命中各验一次；开关「追加 WAN DNS」后 nameserver 列表是并集而非替换 |
| M6 管理面与设备排除 | 策略组/提供者字段深度、增量合并保存、改名联动、自定义节点 8 协议 + 透传、规则提供者、IP/CIDR 排除、批量删除、Read/Apply | 新建规则提供者并引用 → 规则生效；保存面板修改后，订阅带来的未收录 provider 仍在；改策略组名后所有引用同步更新；排除一台设备的 IP 后该设备流量不经 TUN；批量删「未启用节点」只删未启用的 |
| M7 内核多版本 + 历史 + 看门狗 | 保留 3 版可切换；节点选择历史；分级恢复 | 两版内核来回切；切配置再切回后节点选择保留；拔掉上游后看门狗先重载、再重启、最终标红 |
| M8 运维、统计与诊断 | 定时重启、日志上限与三 tab、Rules/GEO/Chnroute 更新、备份恢复、调试页、采样与统计、诊断 zip | 定时重启按期触发并恢复；日志文件不超过上限且三 tab 内容不同；备份可还原到新卷；诊断 zip 含规则集、历史与宿主网络快照 |
| M9 发布 2.1 | 版本与文档收口 | 2.1 镜像可独立升级，2.0 数据卷自动迁移 |

## 7. 风险与待定

1. **`PUT /configs` 恒 204**：运行期失败（TUN 创建失败、端口占用）不会回报，只能靠面板自验与看门狗兜底；这也是模式切换选择重启的原因。
2. **`iptables.enable` 与 `tun.enable` 同时为真时内核直接 `os.Exit(2)`**（`hub/executor/executor.go` 的 iptables 分支）：Overlay 校验必须把这对组合判为非法，否则内核会静默退出并进入崩溃退避。
3. **排除集的两条静默失败**（netns 实测）：provider 名不存在时 TUN 直接建不起来、进程不退；`auto-route/auto-redirect` 关闭时排除集被忽略且无报错。两条都必须由渲染期校验与 apply 自验兜住，不能依赖内核报错。
4. **上游的 GEO 更新先删后搬**（`openclash_geo.sh`）：中途失败会丢旧文件，我们一律原子替换，不照抄。
5. **TUN 模式下 53 是必需项**：宿主若有 systemd-resolved/dnsmasq 占着 53，开 TUN 会被拒；这是有意为之，避免 fake-ip 静默失效。
6. **旁路由**：LAN 设备的网关与 DNS 不指向 ClashFlow 主机时 TUN 收不到流量；只能靠文档与诊断页表达。
7. **规则集与历史快照**要随配置变化原子重写，旧文件不能残留；诊断包与备份必须含它们。
8. **渲染与转换的 YAML 往返**会丢注释与排版：运行配置不承载用户内容，只保证同输入同输出。
9. **`/data/runtime/` 与 `/data/runtime-configs/` 易混**：前者是更新包，后者是渲染产物。
10. **迁移不可逆**：`/data/backup/` 不自动清理，回退需手动还原。
11. **arm/v7**：subconverter 的架构可用性需单独验证，不可用则收窄矩阵并在文档说明。
