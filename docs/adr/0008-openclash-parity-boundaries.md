# OpenClash 对齐的取舍：设备排除走内核规则集，五项不抄

状态：已接受（2026-11-06）；补充 ADR-0007，不改其拓扑与许可结论。

逐文件核对 OpenClash 源码后（`root/usr/share/openclash` 37 个条目：25 个 shell 脚本、8 个 Lua 脚本、两套 UI 与资源目录；15 个 CBI 页面；`settings.lua` 1623 行），确认数据面模式、旁路由流量控制、DNS 深水区、订阅参数面四整块不在原计划内，已补入 2.0/2.1。剩下两件事需要单独记：设备级排除怎么做，以及哪些明确不抄。

## 决定

- **设备级排除走内核规则集，不碰宿主防火墙**。mihomo 的 `tun.route-exclude-address-set` 支持按规则集里的 IP/CIDR 把流量排除出 TUN（需要 nftables 与 `auto-route` + `auto-redirect`，二者本就在 ADR-0005 的部署前提里）。面板生成一份本地规则集文件即可，不需要写 iptables/nftables 规则，也不需要容器额外的宿主写权限。
- **不做 MAC 级与端口级的 LAN/WAN 黑白名单**：它们必须落在宿主防火墙链上，与「容器只碰自己的东西」冲突；IP/CIDR 级已覆盖家用场景（IoT 设备、旁路主机）。
- **五项明确不抄**：流媒体解锁检测与自动选节点、oixCloud 签到、Small Flash Memory、Delay Start On Boot、MAC 级黑白名单。
- **数据面照抄**：六种操作模式（`redir-host` / `fake-ip` × 普通 / `tun` / `tun-mix`）、代理模式 `rule`/`global`/`direct`、完整端口族（http/socks/mixed/redir/tproxy/dns）、UDP 与 QUIC 开关。
- **流量控制照抄**：路由器自身是否走代理、绕过大陆 IP（含自定义 chnroute 源与 mrs/dat 选择）、常见端口代理模式、跳过代理地址、内网直连网段、仅内网允许、接口绑定、旁路由兼容与 masq cache。
- **DNS 全量放 2.1**：6 组服务器、fake-ip/fallback 过滤器、hosts、域名 DNS 策略、respect rules、append WAN/default DNS、缓存与 AAAA 过滤、store-fakeip。2.0 只做 listen/nameserver/fallback/fake-ip-range。
- **订阅参数面分两段**：2.0 做模板（内置 ini 列表 + 自定义 URL）、关键词/排除/广告过滤、emoji/排序/节点类型、自定义请求头、流量与到期展示；2.1 做 Age 解密与在线转换后端兜底。
- **运维组放 2.1**：定时重启、日志大小上限、Rules/GEO/Chnroute 自动更新与自定义源、备份/恢复（含下载运行中配置）、ETag 节流、DNS 与连接调试页。

## 后果

- Overlay 从「四个网关键」膨胀成一套完整的 mihomo 数据面参数面；渲染器必须能表达「模式 × 端口族 × 流量控制」的组合，而不是逐个 if。
- 六种操作模式与 nftables/路由的组合需要逐项实测（2.0 的验收里加一条矩阵）。
- 规则集文件成为新的持久化产物（设备排除 + 绕过大陆），要进备份、要能在诊断包里看到。
- 「不抄」的五项是永久边界：以后有人提流媒体解锁或 MAC 白名单，答案在这份 ADR 里。

## 备选方案（未采用）

- **iptables/nftables 黑白名单**：表达力最强（MAC、端口都能做），但要在容器里改宿主防火墙链，失败时留下孤儿规则，排障面从容器扩到宿主整张表。
- **不做设备级排除**：靠用户改设备网关/DNS 绕开，家用场景可接受，但 IoT 设备往往无法单独配置。
- **只抄「常用的」操作模式**：看起来省事，实际会在用户切换模式时暴露为「面板里没有这个选项」，与「完全取代」冲突。
