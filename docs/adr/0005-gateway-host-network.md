# 网关形态：只支持 host 网络，TUN 默认关闭

状态：已接受（2026-11-06）。

TUN 要接管的是**宿主**的网络栈：`auto-route` 写路由、`auto-redirect` 写 nftables 规则、`dns-hijack` 拦 DNS。在 bridge 网络下这些只发生在容器自己的 netns 里，局域网设备把网关指向宿主时，流量根本进不了容器的 tun 设备——「TUN + 端口映射」在技术上互斥，不是配置问题。

## 决定

- 镜像**只支持 `--network host`**，删除端口映射模式。README 的 `docker run` 模板同时给出 `--cap-add NET_ADMIN` 与 `--device /dev/net/tun`。
- **TUN 默认关闭**（Overlay 的 `tun.enable: false`）：迁移后行为与 V1 一致（`7890` 显式代理 + 面板），由用户在面板里一键开启。降级路径因此始终存在。
- **53 由内核持有，且 TUN 模式下是必需项**：mihomo 的 `dns-hijack` 只劫持进入 tun 设备的流量，LAN 设备指向网关 IP 的 DNS 查询走宿主栈、不经 tun，所以 TUN 模式下 `dns.listen` 必须是 `0.0.0.0:53`，否则 fake-ip 不生效。开启 TUN 时面板先做占用检测（`systemd-resolved`、`dnsmasq`、其他监听者）并给出停用指令，用户确认后才把 `dns.listen` 落到 53；不满足则拒绝开启并说明原因。
- **启动自检**：`8080`、`9090`（必须回环）、TUN 开启时的 `53` 冲突 → 中文报错并拒绝启动；`7890` 冲突、`ip_forward`、nftables 可用性 → 只警告，并进诊断包。
- **TUN 健康监测**：定期检查 tun 设备与默认路由，丢失则自动重启内核一次并标红；连续失败转 `degraded`（复用现有退避与上限）。

## 后果

- Docker Desktop（macOS/Windows）没有 `/dev/net/tun`，V2 不再支持；群晖/其他 NAS 与裸 Linux 网关可用。
- 面板 `8080`、代理 `7890` 直接占宿主端口，同机不能再跑第二个实例。
- `9090` 从「容器内回环」变成「宿主回环」：宿主若有服务占用它，启动自检拒绝启动。
- 内核会往**宿主**装路由与 nftables 规则，排障面从容器扩到宿主；诊断包必须含宿主侧只读快照（接口、路由、nftables 规则、53 监听者），否则「整网不通」无从定位。
- 面板与代理端口的鉴权边界不变：面板仍是单密码 + 会话；代理端口仍对 LAN 免认证。

## 备选方案（未采用）

- **保留 bridge 与端口映射两种模式**：要在 bridge 下实现整网接管，得替宿主编排路由与 iptables 指向容器，等于让面板改写宿主网络栈，且失败时留下一地规则。
- **只做 host 网络，TUN 恒开**：开箱即整网接管，但把「首次部署」变成一次不可回退的网络变更；旧用户迁移时毫无缓冲。
- **强制接管 53**：省掉一次确认，但在 NAS 上撞 `systemd-resolved` 会直接拒绝启动，用户只能读文档自救。
- **走 OpenWrt 安装包（真正的 OpenClash 路线）**：那要求放弃 Python/FastAPI 面板、改用 LuCI（ucode + Lua）与 opkg/apk 打包，等于另起一个项目。
