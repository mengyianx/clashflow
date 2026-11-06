# Config ownership: verbatim Subscription configs, no overwrite layer

状态：已被 [ADR-0004](0004-source-and-runtime-configs.md) 取代（2026-11-06）。V2 引入源配置/运行配置两层与网关级 Overlay；本文保留为 V1 的决策记录。

ClashFlow keeps every Config as a standalone mihomo YAML file: a Subscription refresh rewrites exactly one Config with the fetched content verbatim, Hand-written Configs are never touched by refreshes, and exactly one Active Config runs at a time. We deliberately rejected OpenClash-style merge/overwrite layers (node-segment merging, overwrite modules): hidden merge semantics make "what will my config look like after the next refresh" unanswerable, and a pure YAML editor was an explicit product decision. To change the rules of a Subscription-derived Config, copy it into a Hand-written Config first.

## Considered Options

- **Refresh merges only node segments** (`proxies`/`proxy-groups`) and preserves hand-edited rules/DNS — rejected: that is a hidden merge layer, and shape changes in the fetched file silently invalidate hand edits anyway.
- **Refresh overwrites wholesale, with backup/rollback** — rejected: hand edits then survive only until the next refresh; rollback is a poor substitute for never clobbering.
