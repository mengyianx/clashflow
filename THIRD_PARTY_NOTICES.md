# 第三方组件与许可

ClashFlow 自身以 MIT 发布（见 `LICENSE`）。镜像内包含、或计划包含以下第三方产物；本文件记录各自的许可与义务。

## 运行时包含

| 组件 | 许可 | 形态 | 义务 |
|---|---|---|---|
| [mihomo](https://github.com/MetaCubeX/mihomo) | MIT | 独立进程，二进制随镜像 | 保留版权与许可声明 |
| [metacubexd](https://github.com/MetaCubeX/metacubexd) | MIT | 静态文件，嵌入镜像 | 保留版权与许可声明（`/ui/THIRD_PARTY_NOTICES` 或本文件） |
| [zashboard](https://github.com/Zephyruso/zashboard) | MIT | 静态文件，嵌入镜像 | 同上 |
| [subconverter](https://github.com/tindy2013/subconverter) | **GPL-3.0** | 独立进程，二进制随镜像（V2 起） | 分发镜像时须提供对应源码的获取途径；不改动源码，指向上游对应 release tag 即可；镜像内附 GPL-3.0 全文 |

## 仅移植代码（不随镜像分发）

| 来源 | 许可 | 用法 |
|---|---|---|
| [OpenClash](https://github.com/vernesong/OpenClash) | MIT | 移植其配置生成与覆写逻辑到 Python/TypeScript；移植文件在文件头注明来源与 Copyright (c) 2019-2026 vernesong |

## 说明

- mihomo、metacubexd、zashboard、OpenClash 均为 MIT，与 ClashFlow 的 MIT 兼容，只需保留声明。
- subconverter 是唯一 GPL-3.0 依赖。它与 ClashFlow 以独立进程、HTTP 交互的方式协作，不构成链接或衍生作品；GPL 义务限于该二进制本身：镜像需附带许可文本与源码获取途径。
- 若后续决定内置其他 GPL 组件，须同时更新本文件与镜像构建脚本中的源码提供方式。
