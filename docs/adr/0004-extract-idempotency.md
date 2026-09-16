# ADR-0004: EXTRACT_VIEWPOINT 重跑幂等契约

日期：2026-09-16（Plan #1 eng review D10）｜ 状态：已接受

## Context

抽取任务（EPIC-04）必然重试/重跑：模型升级、prompt 迭代、失败重试。若无幂等契约，同一 source_item 会堆积重复观点，污染时间线与共识。

## Decision

EXTRACT_VIEWPOINT 的幂等键 = `(source_item_id, prompt_version, extractor_version)`。重跑时先删除该键下的旧 candidate 观点（及其证据，CASCADE），再插入新结果；已 reviewed 的观点不在删除范围。

## Consequences

- 同键重跑结果确定；跨键（升级 prompt/模型）保留历史版本，供对比与回滚。
- 删除-插入需在同一事务内完成；实现落在 EPIC-04 的抽取服务。
