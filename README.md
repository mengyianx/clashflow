# ClashFlow

单容器自托管代理栈：mihomo（Kernel）+ 中文管理面板 + 两套运行面板（metacubexd / zashboard）。一条 `docker run` 得到可用代理；订阅、纯 YAML 配置、服务控制全部在面板完成。Controller 只监听容器内回环，浏览器侧永远接触不到 API secret。

## 快速开始（从零到可用）

GHCR 上的镜像包继承仓库的可见性：仓库为私有时镜像同样是私有的，拉取前先登录（PAT 需带 `read:packages` 权限）。镜像 tag `:latest` 指向最近一次手动发布的版本，不是 `flow` 分支的当前提交（见「CI：质量门、镜像与发版」）。

```bash
printf '%s' "$GHCR_PAT" | docker login ghcr.io -u <GitHub 用户名> --password-stdin
```

```bash
docker run -d --name clashflow --restart unless-stopped \
  -e CLASHFLOW_PASSWORD='换成你的面板密码' \
  -v clashflow-data:/data \
  -p 7890:7890 \
  -p 8080:8080 \
  ghcr.io/mengyianx/clashflow:latest
```

1. 打开 `http://<网关IP>:8080`，用 `CLASHFLOW_PASSWORD` 登录。
2. **配置** 页：默认已有一个 `default` 配置（`mixed-port: 7890`、`allow-lan: true`）。可新建（空配置 / 空白模板）、复制为手写配置、编辑保存——保存 = 内核校验 + 原子替换 + 热载，坏配置被拒且内核错误**原样回显**、旧文件不动；热载被控制接口拒绝时旧字节写回磁盘，旧文件同样逐字节不变。
3. **订阅** 页：粘贴订阅 URL 导入（内容必须是完整的 mihomo YAML）；手动刷新、设置自定义 UA 与更新间隔；到期自动刷新，失败保留旧配置并标红原因与失败类别（三类：`network` 网络故障 / `kernel-rejected` 内核拒绝 / `hot-apply-refused` 热载被拒），热载被拒时旧配置逐字节不变，下一轮自动重试。
4. **总览** 页打开 **Runtime Dashboard**：`/ui/` 是 metacubexd，`/zui/` 是 zashboard，两者都免手填端点直连面板反代。
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
| `CLASHFLOW_DATA_DIR` / `CLASHFLOW_WEB_DIST` / `CLASHFLOW_UI_DIST` / `CLASHFLOW_ZUI_DIST` | 否 | 镜像内置路径 | 数据卷、面板前端、两套运行面板产物位置，一般无需修改 |
| `CLASHFLOW_KERNEL` | 否 | `mihomo` | 内核可执行文件名或路径（默认名 `mihomo`，镜像内位于 `/usr/local/bin/mihomo`）；只决定镜像基线，运行时更新装到 `/data/runtime/` 后优先于它。一般无需修改 |
| `CLASHFLOW_GITHUB_API` / `CLASHFLOW_GITHUB_PROXY` | 否 | `https://api.github.com` / 空 | 检查更新的 API 地址与下载/API 统一前缀（GitHub 不稳定时用） |
| `CLASHFLOW_VERSIONS_FILE` | 否 | `webui-versions.json`（镜像内 `/app/webui-versions.json`） | 镜像基线版本清单，只有自建镜像才需要改 |

## 数据卷 `/data`

所有状态都在一个卷里：配置与订阅（`configs/`）、元数据（`clashflow.db`：配置注册表、Active 记录、secret）、内核缓存（`cache.db`，节点选择按代理组名保留）、GEO 数据与 provider 缓存。

- **备份**：拷贝整个 `/data`（含 `runtime/`：运行时更新过的内核与面板）。
- **迁移 / 升级**：挂回同一个卷；镜像内仍有 pin 的基线版本（当前 mihomo `v1.19.32`、metacubexd `v1.273.1`、zashboard `v3.30.0`），内核与运行面板可在「系统」页手动运行时更新（ADR-0003），装进 `runtime/` 且重建容器后保留；升级面板本身仍是拉新镜像重建容器。
- 不挂卷 = 回到零配置状态并重新下载 GEO。

## 功能面

- **配置管理**：多配置并存（每订阅一份派生配置 + 任意手写配置，全局唯一名）。保存 = `mihomo -t` 校验 → 原子替换 →（Active 时）热载，不重启内核；校验被拒时旧文件逐字节不变，热载被控制接口拒绝时旧字节写回磁盘、旧文件同样逐字节不变；切换 Active 走同一校验+热载管线，成功才记录。订阅派生配置可手改，但编辑器会警示“下次刷新将覆盖”，并提供一键“复制为手写配置”。
- **订阅管理**：URL 导入恰生成一份订阅派生配置，刷新只重写它自己；手动 / 定时刷新（全局默认 24h，每订阅可覆盖间隔与 UA）；失败保留旧配置、标红原因与失败类别（三类：`network` 网络故障 / `kernel-rejected` 内核拒绝 / `hot-apply-refused` 热载被拒，类别在原文上方单独一行；非法 YAML 由内核判定，归入 `kernel-rejected`），热载被拒时旧配置逐字节不变，下一轮自动重试，成功即清除。
- **服务控制**：启动 / 停止 / 重启，状态一目了然（`up` / `respawning` / `down` / `degraded`）；内核意外退出按指数退避自动拉起（始终以当前 Active Config）；GEO 自动更新由内核内置调度负责（默认模板与空白模板均已开启）。
- **运行面板**：两套上游运行面板并存——metacubexd 在 `/ui/`（静态模式下自动隐藏其 Profile 管理面），zashboard 在 `/zui/`（首次打开自动指向本面板的 `/api` 反代，并隐藏内核升级与 TUN 开关）。两者都在新标签页打开，节点选择 / 延迟测试 / 连接 / 流量 / 日志各取所需。
- **面板界面**：中文单语；浅色默认、跟随系统、可手动切深色（选择存 `localStorage`）。桌面为左侧栏、手机为底部标签；总览 / 配置 / 订阅 / 编辑器 / 系统五屏 + 两套运行面板（Runtime Dashboard）入口：`/ui/` metacubexd、`/zui/` zashboard。YAML 编辑器带行号与语法高亮。失败提示统一为「中文标题 + 原文」：面板自撰消息映射成中文，内核实名输出原样保留、不翻译不截断。
- **系统与更新**：系统页手动检查 GitHub 最新发布，一键更新内核 / metacubexd / zashboard；更新包装在 `/data/runtime/`，容器重建后保留，每个组件可回滚到上一版。内核更新会自动重启内核，新二进制没起来则回滚旧版。详见 `docs/adr/0003-runtime-updates.md`。
- **安全面**：面板密码 + 服务端会话 cookie（HttpOnly，登出即吊销）；API secret 只在服务端注入反代；Controller 仅回环监听。

完整的边界清单（做什么 / 明确不做 / 量化契约 / 已知取舍）见 [`docs/scope.md`](docs/scope.md)。

## 冒烟自检（对应实现规划 M1–M5）

```bash
# M1 容器与健康：liveness 免登录；登录后 GET /api/service/health → kernel 状态 + Active Config
curl -s http://127.0.0.1:8080/healthz        # {"status": "ok"}

# M2 配置管理：POST /api/panel/configs（新建）· GET/PUT /api/panel/configs/{name}（读/存）
#             · POST /api/panel/configs/{name}/copy · PUT /api/panel/configs/{name}/active
#             坏 YAML → 400 且 detail 为内核错误原文，旧文件逐字节不变

# M3 订阅：POST /api/panel/subscriptions（导入）· POST /api/panel/subscriptions/{name}/refresh
#         失败标红并保留旧配置；间隔覆盖 + CLASHFLOW_SCHEDULER_TICK_SECONDS 可见自动触发

# M4 运行面板：浏览器打开 /ui/ 与 /zui/，切节点 / 延迟测试 / 连接 / 流量 / 日志全走 /api/* 反代（无直连 9090）

# M5 鉴权与交付：未登录访问任意面板路由 / API / WebSocket 被拒；多架构镜像见下
```

## 本地开发与测试

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
bash scripts/fetch-kernel.sh          # 固定版本 mihomo（测试用真实内核）
bash scripts/fetch-ui.sh              # metacubexd 静态包（/ui/）
bash scripts/fetch-zui.sh             # zashboard 静态包（/zui/）
(cd web && npm ci && npm run build)   # 面板前端产物（`/` 的 shell 断言依赖它）
.venv/bin/python -m pytest -q
```

两条容易踩的坑：seam 测试绑定固定端口 9090 / 7890，跑测试前先停掉本机运行的面板或容器；`web/dist` 与三个下载包都是生成物（已 ignore），删掉后上面的准备步骤要重跑。

## CI：质量门、镜像与发版

本地不构建镜像，也不靠本地判断 CI 是否通过。两个 workflow：

| workflow | 触发 | 做什么 |
|---|---|---|
| `Checks` | PR、push `flow` | 前端 `tsc --noEmit` + `vite build`（产物作为 artifact 传给后端 job）；后端 `ruff check` / `ruff format --check` / `mypy` / `pytest`（真实内核 + 两套面板产物） |
| `Image` | PR | 对 `amd64 / arm64 / arm/v7` 各构建一次，**不推送**，只验证 Dockerfile |
| `Image` | 手动 `workflow_dispatch` | 输入版本号后发布镜像并建 GitHub Release |

发版（GitHub → Actions → Image → Run workflow，选 `flow`，填 `vX.Y.Z`；或 CLI）：

```bash
gh workflow run image.yml --ref flow -f version=v1.1.0
```

发版前先确认该 ref 的 `Checks` 为绿。产物：`ghcr.io/<owner>/<repo>:{vX.Y.Z, vX.Y, latest}`，多架构 `linux/amd64`、`linux/arm64`、`linux/arm/v7`，并生成同名 GitHub Release。`latest` 指向**最近一次手动发布的版本**（只有手动发版才更新它），不是 `flow` 分支的当前提交——`flow` 上有更新而未发版时，`latest` 仍是上一个发布版。前端与两套面板的产物在 runner 本机构建（`FROM --platform=$BUILDPLATFORM`），只有内核阶段走 QEMU。

## 限制与折衷（v1 明确取舍）

- 面板纯 HTTP：LAN 明文传输，密码 / cookie 理论上可被同网嗅探——家用场景的明确折衷（见 `docs/adr/0002-runtime-api-via-backend-proxy.md`）。
- 单用户；不做 TUN / 透明网关；代理端口对 LAN 免认证。
- 订阅只接受完整 mihomo YAML（不做 subconverter、Age 解密、节点过滤）。
- 配置内容原样归属用户 / 供应商（`docs/adr/0001-config-ownership-verbatim-yaml.md`）：面板从不改写既有 YAML，没有覆写 / 合并层。订阅内容按 ADR-0001 原样使用——供应商 YAML 里没有 `geo-auto-update` 时 GEO 自动更新不会由面板补写，需要请在订阅内容里自行加上该键。
- `mihomo -t` 校验有联网副作用：配置引用的 GEO 缺失时会尝试下载，下载失败判校验失败。
- 会话保存在面板进程内存：重启面板后需重新登录。
- 管理面板是中文单语，不做语言切换；两套运行面板都是上游界面（metacubexd 自带 en / zh / ru / ja / ko / fr / fa 并跟随浏览器语言，zashboard 自带 zh / en），面板不改写它们。内核自己写出的报错（`mihomo -t` 输出、Controller 拒绝原文）永远是英文，按原样呈现。
