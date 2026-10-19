# ClashFlow

单容器自托管代理栈：mihomo（Kernel）+ 管理面板 + metacubexd 运行面板。一条 `docker run` 得到可用代理；订阅、纯 YAML 配置、服务控制全部在面板完成。Controller 只监听容器内回环，浏览器侧永远接触不到 API secret。

## 快速开始（从零到可用）

```bash
docker run -d --name clashflow --restart unless-stopped \
  -e CLASHFLOW_PASSWORD='换成你的面板密码' \
  -v clashflow-data:/data \
  -p 7890:7890 \
  -p 8080:8080 \
  ghcr.io/mengyianx/clashflow:latest
```

1. 打开 `http://<网关IP>:8080`，用 `CLASHFLOW_PASSWORD` 登录。
2. **Configs** 页：默认已有一个 `default` 配置（`mixed-port: 7890`、`allow-lan: true`）。可新建（空配置 / 空白模板）、复制为手编配置、编辑保存——保存 = 内核校验 + 原子替换 + 热载，坏配置被拒且内核错误**原样回显**、旧文件不动。
3. **Subscriptions** 页：粘贴订阅 URL 导入（内容必须是完整的 mihomo YAML）；手动 Refresh、设置自定义 UA 与更新间隔；到期自动刷新，失败保留旧配置并标红原因，下一轮自动重试。
4. Overview 打开 **Runtime Dashboard**（`/ui/`）：节点选择、延迟测试、连接、流量、日志，免手填端点直连面板反代。
5. LAN 设备把代理指向 `http://<网关IP>:7890`（mixed 端口，HTTP/HTTPS/SOCKS 同口，免认证）。

## 端口

| 端口 | 用途 | 暴露面 |
|---|---|---|
| `7890` | 代理端口（mixed，`allow-lan: true`） | LAN 设备显式代理，免认证 |
| `8080` | 管理面板（纯 HTTP） | LAN；除登录页与 `/healthz` 外全部需要登录 |
| `9090` | 内核 Controller | **仅容器内 `127.0.0.1`**，不经 LAN 暴露；由面板 `/api/*` 反代并在服务端注入 secret |

## 环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `CLASHFLOW_PASSWORD` | 是 | — | 面板密码；缺失时容器拒绝启动并明确报错 |
| `CLASHFLOW_SECRET` | 否 | 随机生成一次并持久化到 `/data` | Controller secret，仅服务端持有 |
| `CLASHFLOW_SCHEDULER_TICK_SECONDS` | 否 | `60` | 订阅调度器巡检间隔（秒） |
| `CLASHFLOW_RESPAWN_BASE_DELAY` | 否 | `1.0` | 内核崩溃自动拉起的指数退避基数（秒） |
| `CLASHFLOW_MAX_RESPAWNS` | 否 | `5` | 连续崩溃拉起次数上限；超限转 `degraded`（面板红字），手动 Start 恢复 |
| `CLASHFLOW_DATA_DIR` / `CLASHFLOW_WEB_DIST` / `CLASHFLOW_UI_DIST` | 否 | 镜像内置路径 | 数据卷与前端/运行面板产物位置，一般无需修改 |

## 数据卷 `/data`

所有状态都在一个卷里：配置与订阅（`configs/`）、元数据（`clashflow.db`：配置注册表、Active 记录、secret）、内核缓存（`cache.db`，节点选择按代理组名保留）、GEO 数据与 provider 缓存。

- **备份**：拷贝整个 `/data`。
- **迁移 / 升级**：挂回同一个卷；内核与运行面板版本随镜像 pin（当前 mihomo `v1.19.32`、metacubexd `v1.273.1`），升级 = 拉新镜像重建容器，不做容器内自升级。
- 不挂卷 = 回到零配置状态并重新下载 GEO。

## 功能面

- **配置管理**：多配置并存（每订阅一份派生配置 + 任意手编配置，全局唯一名）。保存 = `mihomo -t` 校验 → 原子替换 →（Active 时）热载，不重启内核；切换 Active 走同一校验+热载管线，成功才记录。订阅派生配置可手改，但编辑器会警示“下次刷新将覆盖”，并提供一键“复制为手编配置”。
- **订阅管理**：URL 导入恰生成一份订阅派生配置，刷新只重写它自己；手动 / 定时刷新（全局默认 24h，每订阅可覆盖间隔与 UA）；失败保留旧配置、标红原因（网络/非法 YAML/校验错误分类可见），下一轮自动重试，成功即清除。
- **服务控制**：启动 / 停止 / 重启，状态一目了然（`up` / `respawning` / `down` / `degraded`）；内核意外退出按指数退避自动拉起（始终以当前 Active Config）；GEO 自动更新由内核内置调度负责（默认模板已开启）。
- **运行面板**：内嵌 metacubexd（`/ui/`），开箱连接，profile 管理面在静态模式下自动隐藏。
- **安全面**：面板密码 + 服务端会话 cookie（HttpOnly，登出即吊销）；API secret 只在服务端注入反代；Controller 仅回环监听。

## 冒烟自检（对应实现规划 M1–M5）

```bash
# M1 容器与健康：liveness 免登录；登录后 GET /api/service/health → kernel 状态 + Active Config
curl -s http://127.0.0.1:8080/healthz        # {"status": "ok"}

# M2 配置管理：POST /api/panel/configs（新建）· GET/PUT /api/panel/configs/{name}（读/存）
#             · POST /api/panel/configs/{name}/copy · PUT /api/panel/configs/{name}/active
#             坏 YAML → 400 且 detail 为内核错误原文，旧文件逐字节不变

# M3 订阅：POST /api/panel/subscriptions（导入）· POST /api/panel/subscriptions/{name}/refresh
#         失败标红并保留旧配置；间隔覆盖 + CLASHFLOW_SCHEDULER_TICK_SECONDS 可见自动触发

# M4 运行面板：浏览器打开 /ui/，切节点 / 延迟测试 / 连接 / 流量 / 日志全走 /api/* 反代（无直连 9090）

# M5 鉴权与交付：未登录访问任意面板路由 / API / WebSocket 被拒；多架构镜像见下
```

## 构建镜像

```bash
docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 -t clashflow:local .
```

CI（`.github/workflows/ci.yml`）在每次 push 上运行 lint / type-check / 测试，并用 buildx 构建推送多架构镜像到 GHCR（`ghcr.io/<owner>/<repo>`，分支与 commit SHA 标签）。

## 限制与折衷（v1 明确取舍）

- 面板纯 HTTP：LAN 明文传输，密码 / cookie 理论上可被同网嗅探——家用场景的明确折衷（见 `docs/adr/0002-runtime-api-via-backend-proxy.md`）。
- 单用户；不做 TUN / 透明网关；代理端口对 LAN 免认证。
- 订阅只接受完整 mihomo YAML（不做 subconverter、Age 解密、节点过滤）。
- 配置内容原样归属用户 / 供应商（`docs/adr/0001-config-ownership-verbatim-yaml.md`）：面板从不改写既有 YAML，没有覆写 / 合并层。
- `mihomo -t` 校验有联网副作用：配置引用的 GEO 缺失时会尝试下载，下载失败判校验失败。
- 会话保存在面板进程内存：重启面板后需重新登录。
