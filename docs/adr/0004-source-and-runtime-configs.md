# 配置分两层：源配置 + Overlay 渲染成运行配置

状态：已接受（2026-11-06）；取代 [ADR-0001](0001-config-ownership-verbatim-yaml.md)。

ADR-0001 的前提是「磁盘上的 Config 就是内核跑的那份」。V2 要支持整网透明代理，而 TUN、DNS、端口、日志级别是**网关属性**：它们不属于某一份订阅，也不该逼用户在每份 YAML 里重复一遍——订阅配置每次刷新会被原样重写，手写的重复项必然丢失。因此反转 ADR-0001：配置分成源与产物两层，面板拥有一层网关级覆盖。

## 决定

- **Source Config（源配置）**：用户内容，落在 `/data/configs/<name>.yaml`。手写配置永不被改写；订阅配置由唯一订阅在每次刷新时原样重写。
- **Runtime Config（运行配置）**：内核实际跑的文件，落在 `/data/runtime-configs/<name>.yaml`，文件名由源配置名确定性派生（`/data/runtime/` 已被 Runtime Update 的安装目录占用，不复用）。内核的 `-f` 只能指向它，不指向源。运行配置不承载用户内容，删掉会被下次渲染重建。
- **Overlay（网关覆盖）**：全局一份，不分配置（网关属性与配置无关），存在 `clashflow.db`；面板编辑，另提供 YAML 导出/导入以便备份与手工审阅。
- **面板恒胜**：Overlay 显式拥有的键在渲染时覆盖源配置的同名字段；编辑器对源里被覆盖的键标「已覆盖」。未被 Overlay 拥有的键原样透传。
- **键集是最小闭包**：`tun`（`enable`/`stack`/`auto-route`/`auto-redirect`/`dns-hijack`/`strict-route`）、`dns`（`enable`/`listen`/`fake-ip-range`/`enhanced-mode`/`nameserver`/`fallback`）、端口（`mixed-port`/`allow-lan`/`bind-address`）、`log-level`。`external-controller` 与 `secret` 仍由命令行钉死，不属 Overlay。
- **渲染是纯函数**：`render(source_bytes, overlay) → runtime_bytes`，同样输入必出逐字节相同的输出。保存、切配置、订阅刷新三条路径共用它。
- **渲染在校验之前**：管线变成 `源 bytes → render → mihomo -t 校验 → 原子替换运行配置 → 是 Active 则热载`。校验对象只有运行配置；源本身不可解析或与 Overlay 结构冲突，归入新的失败类别 `render-failed`。
- **运行配置只读**：面板可预览，并显示与源配置的 diff；不可编辑。要改就改源或改 Overlay。

## 后果

- 每次 apply 涉及两个文件；失败恢复要让源与运行配置**同时**逐字节保持不变（沿用现有 `atomic_save` + `hot_apply_or_restore` 的恢复实现）。
- 「配置长什么样」与「内核吃什么」可能出现差异，排障必须能一眼看到：面板强制展示 diff，诊断包必须同时包含源、运行配置与 Overlay。
- 订阅刷新的语义不变（原样重写源），但刷新成功后要多一次渲染；渲染失败按刷新失败处理——保留旧运行配置、标类别、下轮重试。
- 源配置的语法错误不再表现为内核的英文报错，而是 `render-failed`；面板要能用中文说清「哪一段渲染不出来」。

## 备选方案（未采用）

- **全量编译**：面板 UI 成为唯一真相，用户 YAML 退化为导入导出格式。等于重写整个编辑面，且要用 UI 表达 mihomo 的大半 schema——与「单用户家用、用户仍想直接写 YAML」冲突。
- **双轨**：每份配置可选「原样」或「托管」。迁移风险最低，但两套语义、两倍测试矩阵长期并存。
- **源优先**（Overlay 只填补缺失键）：保住 ADR-0001 的字面精神，但源里写错的 TUN/DNS 会静默生效，与面板显示不一致——透明代理场景下这类不一致直接表现为「整网断流但面板说正常」。
- **按键可配置权威**：精确，但 UI、测试矩阵与心智负担都翻倍，收益只覆盖极少数人。
- **引入 subconverter / 多订阅合并**：需要第二个常驻进程或大量转换逻辑；且这是「把订阅链接转成配置」，与本次要解决的「把用户配置转成运行配置」是两件事。V2 明确不做（见 `docs/scope.md`）。
