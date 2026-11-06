# ClashFlow v2 实现规划

状态：规划中。决策见 ADR-0004 / 0005 / 0006 / 0007 / 0008，代码未动；实际行为仍以 `README.md` 与 `docs/scope.md`（V1 现状）为准。

目标：**完全取代**一台家用 OpenWrt 上的 OpenClash（旁路由拓扑）。按可验证的节奏拆两版：2.0 能接管日常流量，2.1 对齐管理面。

## 1. 交付切分

| 模块 | 2.0 | 2.1 |
|---|---|---|
| 源配置 / 运行配置 / Overlay 渲染层（ADR-0004） | ✅ | |
| 只支持 host 网络 + TUN + DNS 接管（ADR-0005） | ✅ | |
| 数据面：六种操作模式、`rule/global/direct`、全端口族、UDP/QUIC 开关 | ✅ | |
| 旁路由与流量控制：路由器自身代理、绕过大陆、常见端口模式、内网直连、接口绑定、旁路由兼容、masq cache | ✅ | |
| 订阅转换（内置 subconverter）+ 订阅多 URL | ✅ | |
| 订阅参数面：模板、关键词/排除/广告过滤、emoji/排序/节点类型、自定义请求头、流量与到期 | ✅ | |
| 订阅参数面：Age 加密（走内核 `age-secret-key` + `mihomo age keygen/convert`） | ✅ | |
| 订阅参数面：在线转换后端兜底 | | ✅ |
| DNS 最小集（listen / nameserver / fallback / fake-ip-range） | ✅ | |
| DNS 全量：6 组服务器、fake-ip/fallback 过滤、hosts、域名策略、respect rules、append DNS、缓存与 AAAA、store-fakeip | | ✅ |
| 设备级排除：IP/CIDR → 内核规则集（ADR-0008） | | ✅ |
| Overlay 键组扩到 OpenClash 全量 | | ✅ |
| 节点 / 策略组 / 提供者管理（按源配置作用域 + 一键应用到全部；含 smart、policy_filter、health-check 等字段） | | ✅ |
| 配置生成四段式骨架（段①上线，②③④留接口） | ✅ | |
| 段②③④完整实现：自定义规则注入、sniffer/hosts/命名策略、组与节点增删改 | | ✅ |
| 配置摘要（节点/组/规则数、Age 缺 key）与总览状态页（运行状态、出口 IP、更新入口） | ✅ | |
| 内核多版本（保留 3 版） | | ✅ |
| 看门狗（进程 / TUN / 出网 / DNS 探测 + 出口 IP 解析写 hosts） | | ✅ |
| 统计、诊断 zip、定时任务统一 | | ✅ |
| 运维组：定时重启、日志上限、Rules/GEO/Chnroute 更新、备份恢复、ETag、调试页 | | ✅ |
| 导入 OpenClash 最终 `config.yaml` | ✅ | |
| 破坏性迁移与版本门禁（ADR-0006） | ✅ | |

明确不抄：流媒体解锁检测与自动选节点、oixCloud 签到、MAC 级与端口级黑白名单、Small Flash Memory、Delay Start On Boot。仍不做：多用户、多语言、TLS 终结、面板自更新、多实例、多核心（smart/premium）、bridge 网络、Docker Desktop。

## 2. 运行形态

```text
host 网络（唯一形态）
├── clashflow    (FastAPI, :8080)                # 面板 + /api/* 反代
├── mihomo       (-d /data -f /data/runtime-configs/<active>.yaml
│                 -ext-ctl 127.0.0.1:9090 -secret <secret>)
└── subconverter (127.0.0.1:<随机端口>，仅面板调用)
```

`docker run` 模板：`--network host --cap-add NET_ADMIN --device /dev/net/tun -v clashflow:/data -e CLASHFLOW_PASSWORD=…`。

subconverter 独立守护：崩溃只影响转换，订阅刷新标 `conversion-failed`，面板与内核不受牵连。

## 3. 数据布局

```text
/data
├── clashflow.db          # 元数据 + overlay + stats（PRAGMA user_version）
├── configs/<name>.yaml   # 源配置（用户内容）
├── runtime-configs/      # 运行配置（可重建，内核 -f 指向这里）
├── rulesets/             # 面板生成的规则集：设备排除、绕过大陆
├── runtime/              # Runtime Update 安装目录（ADR-0003，保持原样）
├── converters/           # subconverter 的配置与缓存
├── backup/v<from>-<ts>/  # 迁移前自动备份（不自动清理）
└── cache.db / geoip.metadb / GeoSite.dat / ASN.mmdb / providers/
```

`/data/rulesets/` 是新的持久化产物，进备份与诊断包；`runtime/` 与 `runtime-configs/` 只差一个后缀，代码与清理逻辑不能互认。

## 4. 模块增量

```text
clashflow/
├── render.py      # 四段管线：键合并 → 规则注入 → sniffer/hosts/命名策略 → 组与节点增删改；按段报错
├── summary.py     # 配置摘要：节点/组/规则计数、Age 缺 key（校验后展示）
├── overlay.py     # Overlay 读写、YAML 导出/导入、被覆盖键标注
├── datapath.py    # 操作模式 ×端口族× UDP/QUIC 的键映射（六种组合）
├── traffic.py     # 流量控制与旁路由参数：绕过大陆、常见端口、内网直连、接口绑定
├── acl.py         # 设备排除：IP/CIDR → /data/rulesets 并向 tun.route-exclude-address-set 注册
├── subscribe.py   # 多 URL 抓取 → 转换（或 pass-through）→ 源配置；ETag 节流
├── converter.py   # subconverter 子进程守护、模板与过滤参数、调用与结果校验
├── dnsconf.py     # DNS 全量：6 组服务器、过滤器、hosts、域名策略（2.1）
├── selfcheck.py   # 8080/9090/(TUN 时的)53 拒启；7890/ip_forward/nftables 警告
├── providers.py   # 代理/规则提供者的表单与文件（2.1）
├── watchdog.py    # 进程/TUN/出网/DNS 探测与重启（2.1）
├── stats.py       # 60 s 采样 /traffic 与 /connections，按组聚合，7 天清理（2.1）
├── diagnose.py    # 诊断 zip（含宿主网络只读快照与规则集）（2.1）
├── debug.py       # DNS 调试、连接调试、网络详情（2.1）
├── db.py          # user_version 迁移链、订阅↔源配置显式关联、stats 表
├── apply.py       # render → validate → 原子写运行配置 → 热载/回滚
├── kernel.py      # -f 指向运行配置；TUN 健康检查
└── updates.py     # 保留最近 3 版内核与运行面板；Rules/GEO/Chnroute 更新（2.1）
web/src/pages/
├── Gateway/       # 数据面模式、端口族、流量控制、TUN/DNS 最小集、导入导出
├── Runtime/       # 运行配置只读预览 + 与源配置 diff
├── Subscriptions/ # 多 URL、模板、过滤、流量与到期（V1 页面扩展）
├── DNS/           # 6 组服务器与过滤器（2.1）
├── Providers/     # 提供者与策略组表单（2.1）
├── Stats/         # 总量与按组曲线（2.1）
├── Diagnostics/   # 诊断导出与调试页（2.1）
```

界面保留 V1 的五屏（总览 / 配置 / 订阅 / 编辑器 / 系统），新增网关、DNS、提供者、统计、诊断五页；总览补运行状态、出口 IP 与配置摘要；不按 OpenClash 的菜单重排——熟悉感归运行面板，管理面板沿用既有信息架构。

## 5. 关键流程

1. **启动**：读 `user_version` → 需要则备份并迁移 → 自检端口/环境 → 起 subconverter → 渲染 Active 源配置（含数据面与流量控制键、注册排除规则集）→ spawn 内核 → 探活 → 起调度、采样与看门狗。
2. **保存源配置**：候选源 bytes → `render` → `mihomo -t` 校验 → 原子写源 → 原子写运行配置 → 是 Active 则热载；任一步失败，源与运行配置都逐字节保持旧值。
3. **改 Overlay**：校验 → 落库 → 需要时先落规则集文件 → 重渲染 Active → 热载（`tun` 段能否热载需实测，不行则重启内核并走现有恢复路径）。
4. **订阅刷新**：多 URL 抓取（带 ETag，未变则跳过）→ 判断形态（完整 YAML 直接 pass-through；分享链接/base64 列表走 subconverter，套模板与过滤参数）→ 转换结果必须是完整 mihomo YAML → `render` → 校验 → 原子写源 → 原子写运行配置 → 是 Active 则热载。转换失败标 `conversion-failed`，不落盘，下轮重试。
5. **导入 OpenClash**：读取用户上传的最终 `config.yaml` → 作为一份新源配置落盘 → 渲染 → 校验；订阅与覆写由用户在新面板重建（不逆向 uci）。
6. **切换 Active**：渲染目标源 → 校验 → 写运行配置 → 热载 → 成功后记录。节点选择仍按代理组名走内核 `cache.db`。
7. **TUN 与看门狗**：30 s 检查 tun 设备、默认路由、出网与 DNS 解析；单次失败重启内核一次，连续失败转 `degraded`（复用现有退避与 `CLASHFLOW_MAX_RESPAWNS`）。
8. **统计**：60 s 读一次 `/traffic` 与 `/connections`，聚合成「总量 + 按代理组」入 `stats` 表；启动与每日各清理超过 7 天的行。
9. **诊断**：打包版本、overlay、源与运行配置及 diff、规则集、最近日志、失败类别、宿主网络只读快照（`ip -j addr`、`ip -j route`、`nft list ruleset`、53 监听者）。
10. **配置摘要**：每次校验通过后统计节点数、组数、规则数、Age 是否缺 key，写进配置记录，总览与订阅列表直接展示。

## 6. 里程碑与验收

### 2.0

| 里程碑 | 内容 | 验收（可运行 smoke） |
|---|---|---|
| M1 渲染层与迁移 | `render.py` 四段骨架 / `overlay.py` / `summary.py` / 迁移链 / apply 管线改造；Overlay 页、运行配置 diff 页、总览状态与摘要 | 用 v1 数据卷启动：自动备份并迁移、内核以运行配置起来；改 `log-level` 后热载且源配置逐字节不变；Overlay 写非法值被拒且两份文件都不变；坏配置被拒时错误带段名、摘要不更新 |
| M2 数据面与旁路由 | 六种操作模式、代理模式、全端口族、UDP/QUIC；路由器自身代理、绕过大陆、常见端口、内网直连、接口绑定、旁路由兼容 | 六种模式各起一次，每种都能让一台 LAN 设备经 TUN 出网；切到「绕过大陆」后国内直连、国外走代理（用 `curl` 对两个目标验证）；端口族每项 `ss -lntp` 可见且与面板一致 |
| M3 订阅转换与参数面 | subconverter 内置与守护、多 URL、ETag、模板、关键词/排除/广告过滤、emoji/排序/节点类型、自定义头、流量与到期、Age 加密订阅 | 导入分享链接订阅 → 生成源配置并可激活；加第二个 URL 后节点合并；套 ACL4SSR 模板后规则组出现；关键词过滤生效；`mihomo age keygen` 生成 key 后能导入加密订阅；subconverter 被杀 → 标 `conversion-failed`、旧配置不变 |
| M4 导入与发布 2.0 | OpenClash `config.yaml` 导入、迁移说明、README 重写、多架构 | 用导出的 OpenClash 配置导入后整网可用；按新 README 从零部署成功 |

### 2.1

| 里程碑 | 内容 | 验收（可运行 smoke） |
|---|---|---|
| M5 覆写全量与 DNS | Overlay 全量键组；6 组 DNS 服务器、过滤器、hosts、域名策略、respect rules、append DNS、缓存与 AAAA | 每类键组改一项后 diff 只出现该键；两组 DNS 服务器分流可验证（`dig` 走不同上游）；fake-ip 过滤器命中与不命中各验一次 |
| M6 管理面与设备排除 | 策略组/提供者字段深度、自定义节点、规则提供者、IP/CIDR 排除规则集 | 新建规则提供者并引用 → 规则生效；策略组改 `policy_filter` 后热载；排除一台设备的 IP 后该设备流量不经 TUN，其余不受影响 |
| M7 内核多版本 + 看门狗 | 保留 3 版可切换；连通性与 DNS 探测 | 两版内核来回切；切到坏二进制自动回滚；拔掉上游后看门狗重启内核并标红 |
| M8 运维、统计与诊断 | 定时重启、日志上限、Rules/GEO/Chnroute 更新、备份恢复、调试页、采样与统计、诊断 zip | 定时重启按期触发并恢复；日志文件不超过上限；备份可还原到新卷；诊断 zip 含规则集与宿主网络快照 |
| M9 发布 2.1 | 版本与文档收口 | 2.1 镜像可独立升级，2.0 数据卷自动迁移 |

## 7. 风险与待定

1. **六种操作模式 × nftables/路由的组合**必须逐项实测；任何一项没验证过，就不能写进 README 的「支持」列表。
2. **subconverter 常驻**会占内存并引入新的崩溃面：独立守护与状态展示是硬要求。
3. **GPL-3.0 合规**：分发镜像必须同时给出 subconverter 的源码获取途径与许可文本（见 `THIRD_PARTY_NOTICES.md`）。
4. **`tun` 段能否经 `PUT /configs?force=true` 热载**未实测；不行则切 TUN 一律重启内核。
5. **旁路由**：LAN 设备的网关与 DNS 不指向 ClashFlow 主机时 TUN 收不到流量；只能靠文档与诊断页表达。
6. **规则集产物**（设备排除、绕过大陆）要随 Overlay 变化原子重写，旧文件不能残留；诊断包与备份必须含它们。
7. **渲染与转换的 YAML 往返**会丢注释与排版：运行配置不承载用户内容，只保证同输入同输出。
8. **`/data/runtime/` 与 `/data/runtime-configs/` 易混**：前者是更新包，后者是渲染产物。
9. **迁移不可逆**：`/data/backup/` 不自动清理，回退需手动还原。
10. **arm/v7**：subconverter 的架构可用性需单独验证，不可用则收窄矩阵并在文档说明。
