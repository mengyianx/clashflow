# ClashFlow v2 实现规划

状态：规划中。决策已定（ADR-0004 / 0005 / 0006），代码未动；实际行为仍以 `README.md` 与 `docs/scope.md`（V1 现状）为准。

## 1. 目标

从「管一个代理端口的面板」变成「管一台网关的透明代理栈」：用户写的 YAML 是源，网关级参数（TUN / DNS / 端口 / 日志）归面板，内核跑的是两者渲染出来的运行配置。

V1 的四条明确不做被本次反转（其余照旧）：

| V1 不做 | V2 |
|---|---|
| TUN / 透明网关 | 做，TUN 默认关、面板一键开（ADR-0005） |
| 覆写 / 合并层 | 做，但只覆盖网关级最小键集，且面板恒胜（ADR-0004） |
| 流量历史 / 统计 | 做，60 s 采样、按代理组、保留 7 天 |
| OpenClash 式诊断报告 | 做，一键导出全量 zip（含宿主网络只读快照） |

仍然不做：订阅转换（链接/base64 → 配置）、多订阅合并、subconverter、多用户、多语言、TLS 终结、面板自更新、多实例、规则与策略组的可视化编辑、多核心（smart/premium）、bridge 网络、Docker Desktop。

## 2. 运行形态

```text
host 网络（唯一形态）
├── clashflow   (FastAPI, :8080)                  # 面板 + /api/* 反代
├── mihomo      (-d /data -f /data/runtime-configs/<active>.yaml
│                -ext-ctl 127.0.0.1:9090 -secret <secret>)
└── TUN 开启时：mihomo 在宿主装 tun 设备、路由与 nftables 规则
```

`docker run` 模板：`--network host --cap-add NET_ADMIN --device /dev/net/tun -v clashflow:/data -e CLASHFLOW_PASSWORD=…`。

## 3. 数据布局

```text
/data
├── clashflow.db          # 元数据 + overlay + stats（PRAGMA user_version）
├── configs/<name>.yaml   # 源配置（用户内容）
├── runtime-configs/      # 运行配置（可重建，内核 -f 指向这里）
├── runtime/              # Runtime Update 安装目录（ADR-0003，保持原样）
├── backup/v<from>-<ts>/  # 迁移前自动备份（不自动清理）
├── cache.db / geoip.metadb / GeoSite.dat / ASN.mmdb / providers/
```

`runtime/` 与 `runtime-configs/` 只差一个后缀，代码与文档里必须写全路径，避免误删。

## 4. 模块增量

```text
clashflow/
├── render.py      # 纯函数：源 bytes + Overlay → 运行 bytes；渲染失败抛 RenderFailed
├── overlay.py     # Overlay 读写、YAML 导出/导入、被覆盖键的标注
├── selfcheck.py   # 启动自检：8080/9090/(TUN 时的)53 拒启；7890/ip_forward/nftables 警告
├── stats.py       # 60 s 采样 /traffic 与 /connections，按代理组聚合，7 天清理
├── diagnose.py    # 诊断 zip 组装（版本/overlay/源+运行+diff/日志/失败类别/宿主网络快照）
├── db.py          # user_version 迁移链、订阅↔源配置显式关联、stats 表
├── apply.py       # 管线改为 render → validate → 原子写运行配置 → 热载/回滚
├── kernel.py      # -f 指向运行配置；TUN 设备/默认路由健康检查与一次性自动重启
└── updates.py     # 保留最近 3 版内核与运行面板，面板内切换
web/src/pages/
├── Gateway/       # Overlay 编辑（TUN / DNS / 端口 / 日志）+ 导入导出
├── Runtime/       # 运行配置只读预览 + 与源配置的 diff
├── Stats/         # 总量与按代理组曲线
└── Diagnostics/   # 诊断导出入口（并入系统页亦可）
```

## 5. 关键流程

1. **启动**：读 `user_version` → 需要则备份并迁移 → 自检端口/环境 → 渲染 Active 源配置 → spawn 内核 → 探活 → 起调度与采样。
2. **保存源配置**：候选源 bytes → `render` → `mihomo -t` 校验运行 bytes → 原子写源 → 原子写运行配置 → 是 Active 则热载；任一步失败，源与运行配置都逐字节保持旧值。渲染失败 = 新失败类别 `render-failed`。
3. **改 Overlay**：校验 → 落库 → 重渲染 Active → 热载（`tun` 段能否热载需 M2 实测，不行则重启内核并复用现有恢复路径）。
4. **订阅刷新**：fetch → `render` → 校验 → 原子写源 → 原子写运行配置 → 是 Active 则热载。刷新后渲染失败按刷新失败处理：不落盘、标 `render-failed`、下轮重试。
5. **切换 Active**：渲染目标源 → 校验 → 写运行配置 → 热载 → 成功后记录。节点选择仍按代理组名走内核 `cache.db`。
6. **TUN 健康**：30 s 检查 tun 设备与默认路由；丢失则重启内核一次并标红，连续失败转 `degraded`（复用现有退避与 `CLASHFLOW_MAX_RESPAWNS`）。
7. **统计**：60 s 读一次 `/traffic` 与 `/connections`，聚合成「总量 + 按代理组」两行数据入 `stats` 表；启动时与每日各清理一次超过 7 天的行。
8. **诊断**：把上表列出的内容打进 zip 供下载；宿主网络快照只读（`ip -j addr`、`ip -j route`、`nft list ruleset`、53 端口监听者），不执行任何写操作。

## 6. 里程碑与验收

| 里程碑 | 内容 | 验收（可运行 smoke） |
|---|---|---|
| M1 渲染层与迁移 | `render.py`/`overlay.py`/迁移链/apply 管线改造；面板 Overlay 页与运行配置 diff 页 | 用 v1 数据卷启动：自动生成备份、内核以运行配置起来；改 Overlay 的 `log-level` 后热载生效且源配置逐字节不变；Overlay 写非法值被拒且两份文件都不变；订阅刷新渲染失败时订阅标 `render-failed` |
| M2 host 网络与 TUN | 唯一部署形态、自检、53 占用检测与指令、TUN 健康监测 | `--network host --cap-add NET_ADMIN --device /dev/net/tun` 起容器；面板开 TUN 后宿主出现 tun 设备与路由，另一台 LAN 设备改网关后能上网；关 TUN 回到 `7890` 显式代理；手动 `ip link del` tun 后 30 s 内自动恢复或转 `degraded` |
| M3 内核多版本 | Runtime Update 扩为保留 3 版、面板内切换 | 装两版内核可来回切；切到坏二进制自动回滚并报错；关闭状态下换版本不替用户启动 |
| M4 统计 / 诊断 / 定时 | 采样与清理、统计页、诊断 zip、定时任务统一 | 运行 10 分钟后统计页有数据且按组聚合正确；诊断 zip 可下载且含宿主网络快照；定时任务在日志里可观察 |
| M5 发布 2.0 | 镜像、README 重写、迁移说明、三架构验证 | 按新 README 从零部署成功；v1 卷升级后按迁移说明可回退 |

## 7. 风险与待定

1. **`tun` 段能否经 `PUT /configs?force=true` 热载**未实测。不行则切 TUN 一律重启内核，失败按现有「换版本失败」的恢复路径回滚。
2. **host 网络下 9090 与宿主服务冲突**：自检拒启是唯一防线，错误信息必须点名端口与可能的占用者。
3. **渲染的确定性**：YAML 往返会丢注释与原始排版。运行配置不承载用户内容，所以只保证「同输入同输出」，不保证保留源里的注释——diff 页需要说明这一点。
4. **`/data/runtime/` 与 `/data/runtime-configs/` 易混**：前者是运行时更新包，后者是渲染产物；备份与清理逻辑不能互认。
5. **迁移不可逆**：`/data/backup/` 不自动清理，卷会持续变大；迁移说明必须写清回退步骤。
6. **统计表增长**：家用场景下 7 天数据量很小，但采样失败（内核未就绪、反代断开）要有静默跳过，不能把 tick 卡死。
7. **arm/v7**：host 网络与 TUN 不引入新依赖，但仍按「尽力而为」验证。
