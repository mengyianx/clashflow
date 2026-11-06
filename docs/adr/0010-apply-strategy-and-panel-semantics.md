# 应用策略与面板语义：模式切换重内核、apply 后自验、增量合并

状态：已接受（2026-11-06）；细化 ADR-0004 / 0005 / 0009 的执行细节。

上游的行为与我们的数据面不是一对一可抄的关系，逐项核对后固定以下几条，避免实现时各写一套。

## 决定

- **模式切换重启内核**：六种操作模式、TUN 开关、DNS 增强模式（fake-ip / redir-host）变更一律重启内核；端口、日志级别、规则、Overlay 其余键走热载（`PUT /configs?force=true`）。与上游一致：它没有 tun/dns 的热载路径，模式切换就是整包 restart。
- **重载限流**：5 分钟内最多 10 次热载，超出直接拒绝并提示，防前端抖动造成重启循环。
- **apply 后必须自验**：mihomo 的 `PUT /configs` 在解析成功后**恒返回 204**——`hub/route/configs.go` 的 `updateConfigs` 调用 `executor.ApplyConfig` 后不接收任何失败，TUN 创建失败、端口被占都只写进内核日志。因此面板在每次 apply 后自查三件事：TUN 开启时设备与路由存在、声明的端口在监听、内核日志没有新的失败行；任一不满足按 `hot-apply-refused` 处理并回滚到旧运行配置。
- **DNS 语义**：Overlay 的 DNS 字段**替换**源配置对应键；另设「追加系统 DNS」「追加 WAN DNS」两个开关走**追加**。对应上游的 `enable_custom_dns` 与 `append_wan_dns`/`append_default_dns`。
- **面板保存 = 增量合并**：策略组、节点、提供者只改面板收录的条目；订阅带来但面板未收录的项原样保留。上游 `uci.rb:728-752` 用面板三键整体替换，会把未收录项直接删掉——不照抄。
- **节点选择历史按配置保存**：切换配置前把 mihomo 的 `cache.db` 存成该配置的快照，切回时还原并与内核 `profile.store-selected` 对齐；同名组优先。V1 只靠组名匹配，切配置即丢选择。
- **自定义节点范围**：常用 8 种协议（ss / ssr / vmess / vless / trojan / hysteria2 / tuic / anytls）逐字段；其余协议走「分享链接导入 + Other Parameters 透传」，不重写上游 1856 行的全协议表单。
- **移植的健壮性规则**（上游已有，我们照做）：YAML 首行 base64 自动解码、`protocol-param` 与 `short-id` 强制加引号、`!str` 转义修正；规则注入用三锚点（`DST-PORT,80` / 首个 `MATCH|FINAL` / 首个 `GEOIP`）；规则目标组不存在则跳过并告警；`IP-CIDR` 类规则自动补 `/32`、`/128`；空策略组兜底为 `proxies: [DIRECT]`；组与节点重名去重；改组名时同步改所有引用。
- **TUN 组合用 netns 端到端用例验证**：`unshare -rn` + 真实内核二进制即可建 tun、装路由与 nftables 规则，不需要 root，也不碰宿主网络。用例覆盖四组合的设备与路由、缺少 provider 时自验能否抓到、`auto-route/auto-redirect` 关闭时是否拒绝配置。进 CI；runner 不支持 user namespace 时跳过并在发布前手工跑。
- **校验三重**：`mihomo -t` + YAML 可解析 + 必须含 `proxies` 或 `proxy-providers`，否则判空订阅失败。

## 后果

- apply 从「发一条请求」变成「发一条请求 + 三步自验」，失败分类不变但错误定位更准。
- 模式切换会断连数秒，面板必须显式提示「切换模式会短暂中断」。
- 增量合并要求保存前先读回源配置并与面板条目做三方合并，冲突时面板胜、未知项保留。
- 节点历史快照与内核 `cache.db` 是两份真相，切换时必须先写快照再换配置，否则会丢。

## 备选方案（未采用）

- **全部热载**：`PUT /configs` 确实会 `ReCreateTun`（`hub/executor/executor.go` 的 `updateTun` 无视 `force`），但 tun 重建失败时没有任何回报，只能靠看门狗兜底；模式切换这种低频操作不值得冒这个险。
- **全部重启**：改日志级别也断连接，与「热载」能力的存在意义冲突。
- **DNS 按键可选 replace/append**：UI 与测试矩阵翻倍，收益只覆盖极少数人。
- **照抄上游整体替换保存**：会静默删除订阅带来的 provider/节点，是最不该继承的行为。
