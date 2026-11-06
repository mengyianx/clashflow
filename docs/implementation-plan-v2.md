# ClashFlow v2 实现规划

状态：规划中。决策见 ADR-0004 / 0005 / 0006 / 0007，代码未动；实际行为仍以 `README.md` 与 `docs/scope.md`（V1 现状）为准。

目标：**完全取代**一台家用 OpenWrt 上的 OpenClash（旁路由拓扑）。按可验证的节奏拆两版：2.0 能接管日常流量，2.1 对齐管理面。

## 1. 交付切分

| 模块 | 2.0 | 2.1 |
|---|---|---|
| 源配置 / 运行配置 / Overlay 渲染层（ADR-0004） | ✅ | |
| 只支持 host 网络 + TUN + DNS 接管（ADR-0005） | ✅ | |
| 订阅转换（内置 subconverter）+ 订阅多 URL | ✅ | |
| Overlay 键组扩到 OpenClash 全量 | | ✅ |
| 节点 / 策略组 / 提供者（代理与规则）管理 | | ✅ |
| 内核多版本（保留 3 版） | | ✅ |
| 看门狗（进程 / TUN / 出网 / DNS 探测） | | ✅ |
| 统计、诊断 zip、定时任务统一 | | ✅ |
| 导入 OpenClash 最终 `config.yaml` | ✅ | |
| 破坏性迁移与版本门禁（ADR-0006） | ✅ | |

仍不做：多用户、多语言、TLS 终结、面板自更新、多实例、多核心（smart/premium）、bridge 网络、Docker Desktop、节点自动切换与 OpenClash 的场景脚本。

## 2. 运行形态

```text
host 网络（唯一形态）
├── clashflow    (FastAPI, :8080)                # 面板 + /api/* 反代
├── mihomo       (-d /data -f /data/runtime-configs/<active>.yaml
│                 -ext-ctl 127.0.0.1:9090 -secret <secret>)
└── subconverter (127.0.0.1:<随机端口>，仅面板调用，V2 起)
```

`docker run` 模板：`--network host --cap-add NET_ADMIN --device /dev/net/tun -v clashflow:/data -e CLASHFLOW_PASSWORD=…`。

subconverter 独立守护：崩溃只影响转换，订阅刷新标 `conversion-failed`，面板与内核不受牵连。

## 3. 数据布局

```text
/data
├── clashflow.db          # 元数据 + overlay + stats（PRAGMA user_version）
├── configs/<name>.yaml   # 源配置（用户内容）
├── runtime-configs/      # 运行配置（可重建，内核 -f 指向这里）
├── runtime/              # Runtime Update 安装目录（ADR-0003，保持原样）
├── converters/           # subconverter 的配置与缓存
├── backup/v<from>-<ts>/  # 迁移前自动备份（不自动清理）
└── cache.db / geoip.metadb / GeoSite.dat / ASN.mmdb / providers/
```

`runtime/` 与 `runtime-configs/` 只差一个后缀，代码与清理逻辑不能互认。

## 4. 模块增量

```text
clashflow/
├── render.py      # 纯函数：源 bytes + Overlay → 运行 bytes；失败抛 RenderFailed
├── overlay.py     # Overlay 读写、YAML 导出/导入、被覆盖键标注
├── subscribe.py   # 多 URL 抓取 → 转换（或 pass-through）→ 源配置
├── converter.py   # subconverter 子进程守护、配置生成、调用与结果校验
├── selfcheck.py   # 8080/9090/(TUN 时的)53 拒启；7890/ip_forward/nftables 警告
├── providers.py   # 代理/规则提供者的表单与文件（2.1）
├── watchdog.py    # 进程/TUN/出网/DNS 探测与重启（2.1）
├── stats.py       # 60 s 采样 /traffic 与 /connections，按组聚合，7 天清理（2.1）
├── diagnose.py    # 诊断 zip（含宿主网络只读快照）（2.1）
├── db.py          # user_version 迁移链、订阅↔源配置显式关联、stats 表
├── apply.py       # render → validate → 原子写运行配置 → 热载/回滚
├── kernel.py      # -f 指向运行配置；TUN 健康检查
└── updates.py     # 保留最近 3 版内核与运行面板（2.1）
web/src/pages/
├── Gateway/       # Overlay 编辑（TUN / DNS / 端口 / 日志 → 2.1 扩到全量）+ 导入导出
├── Runtime/       # 运行配置只读预览 + 与源配置 diff
├── Subscriptions/ # 多 URL、转换设置、失败类别（V1 页面扩展）
├── Providers/     # 提供者与策略组表单（2.1）
├── Stats/         # 总量与按组曲线（2.1）
└── Diagnostics/   # 诊断导出入口（2.1）
```

界面保留 V1 的五屏（总览 / 配置 / 订阅 / 编辑器 / 系统），新增网关、提供者、统计、诊断四页；不按 OpenClash 的菜单重排——熟悉感归运行面板，管理面板沿用既有信息架构。

## 5. 关键流程

1. **启动**：读 `user_version` → 需要则备份并迁移 → 自检端口/环境 → 起 subconverter → 渲染 Active 源配置 → spawn 内核 → 探活 → 起调度、采样与看门狗。
2. **保存源配置**：候选源 bytes → `render` → `mihomo -t` 校验 → 原子写源 → 原子写运行配置 → 是 Active 则热载；任一步失败，源与运行配置都逐字节保持旧值。
3. **改 Overlay**：校验 → 落库 → 重渲染 Active → 热载（`tun` 段能否热载需实测，不行则重启内核并走现有恢复路径）。
4. **订阅刷新**：多 URL 抓取 → 判断形态（完整 YAML 直接 pass-through；分享链接/base64 列表走 subconverter）→ 转换结果必须是完整 mihomo YAML → `render` → 校验 → 原子写源 → 原子写运行配置 → 是 Active 则热载。转换失败标 `conversion-failed`，不落盘，下轮重试。
5. **导入 OpenClash**：读取用户上传的最终 `config.yaml` → 作为一份新源配置落盘 → 渲染 → 校验；订阅与覆写由用户在新面板重建（不逆向 uci）。
6. **切换 Active**：渲染目标源 → 校验 → 写运行配置 → 热载 → 成功后记录。节点选择仍按代理组名走内核 `cache.db`。
7. **TUN 与看门狗**：30 s 检查 tun 设备、默认路由、出网与 DNS 解析；单次失败重启内核一次，连续失败转 `degraded`（复用现有退避与 `CLASHFLOW_MAX_RESPAWNS`）。
8. **统计**：60 s 读一次 `/traffic` 与 `/connections`，聚合成「总量 + 按代理组」入 `stats` 表；启动与每日各清理超过 7 天的行。
9. **诊断**：打包版本、overlay、源与运行配置及 diff、最近日志、失败类别、宿主网络只读快照（`ip -j addr`、`ip -j route`、`nft list ruleset`、53 监听者）。

## 6. 里程碑与验收

### 2.0

| 里程碑 | 内容 | 验收（可运行 smoke） |
|---|---|---|
| M1 渲染层与迁移 | `render.py` / `overlay.py` / 迁移链 / apply 管线改造；Overlay 页与运行配置 diff 页 | 用 v1 数据卷启动：自动备份并迁移、内核以运行配置起来；改 `log-level` 后热载且源配置逐字节不变；Overlay 写非法值被拒且两份文件都不变 |
| M2 host 网络与 TUN/DNS | 唯一部署形态、自检、53 占用检测与指令、TUN 健康检查 | `--network host --cap-add NET_ADMIN --device /dev/net/tun` 起容器；开 TUN 后宿主出现 tun 与路由，另一台 LAN 设备改网关后能上网；关 TUN 回到 `7890` 显式代理；删 tun 设备后 30 s 内恢复或转 `degraded` |
| M3 订阅转换 | subconverter 内置与守护、多 URL、pass-through、`conversion-failed` | 导入一条分享链接订阅 → 生成源配置并可切换激活；同一订阅加第二个 URL 后节点合并；subconverter 进程被杀 → 刷新标 `conversion-failed`、旧配置不变、面板与内核不受影响 |
| M4 导入与发布 2.0 | OpenClash `config.yaml` 导入、迁移说明、README 重写、多架构 | 用导出的 OpenClash 配置导入后整网可用；按新 README 从零部署成功 |

### 2.1

| 里程碑 | 内容 | 验收（可运行 smoke） |
|---|---|---|
| M5 覆写全量 | general / DNS / rules / proxy-groups / proxy-providers / rule-providers 键组 | 每类键组改一项后运行配置的 diff 只出现该键；源配置不变 |
| M6 管理面 | 提供者与策略组表单、自定义节点、规则提供者 | 新建一个规则提供者并引用 → 内核规则生效；策略组表单改动后热载成功 |
| M7 内核多版本 + 看门狗 | 保留 3 版可切换；连通性与 DNS 探测 | 两版内核来回切；切到坏二进制自动回滚；拔掉上游后看门狗重启内核并标红 |
| M8 统计 / 诊断 / 定时 | 采样与清理、统计页、诊断 zip、定时任务统一 | 运行 10 分钟后按组曲线有数据；诊断 zip 含宿主网络快照 |
| M9 发布 2.1 | 版本与文档收口 | 2.1 镜像可独立升级，2.0 数据卷自动迁移 |

## 7. 风险与待定

1. **subconverter 常驻**会占内存并引入一个新的崩溃面：必须有独立守护与状态展示，且转换失败不能污染源配置。
2. **GPL-3.0 合规**：分发镜像必须同时给出 subconverter 的源码获取途径与许可文本（见 `THIRD_PARTY_NOTICES.md`），否则整套分发不成立。
3. **`tun` 段能否经 `PUT /configs?force=true` 热载**未实测；不行则切 TUN 一律重启内核。
4. **旁路由**：LAN 设备的网关与 DNS 不指向 ClashFlow 主机时 TUN 收不到流量；只能靠文档与诊断页表达，代码无法替代。
5. **渲染与转换的 YAML 往返**会丢注释与排版：运行配置不承载用户内容，只保证同输入同输出。
6. **`/data/runtime/` 与 `/data/runtime-configs/` 易混**：前者是更新包，后者是渲染产物。
7. **迁移不可逆**：`/data/backup/` 不自动清理，回退需手动还原。
8. **arm/v7**：新增 subconverter 的架构可用性需单独验证，不可用则收窄矩阵并在文档说明。
