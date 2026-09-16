# ADR-0001: V1 单一 Python 发行包

日期：2026-09-16 ｜ 状态：已接受

## Context

执行计划 §0.1 的目录树把服务放在根级 `services/media|transcription|llm`、worker 放 `apps/worker/`、共享包放 `packages/`。V1 只有 API 与 worker 两个消费者，且共用同一套 repository 代码；多目录多 editable install 会引入跨目录 import 与打包复杂度。

## Decision

V1 只维护一个 Python 发行包：`apps/api`（包名 `app`），内部分层 `core / db / domain / repositories / worker`。执行计划的 `services/*` 映射为 `app/services/*`；根级 `services/`、`packages/`、`apps/worker/` 在真正出现第二个消费者前不创建。

## Consequences

- 单一 editable install，import 路径简单，CI/lint/mypy 配置单点。
- 若未来 worker 独立伸缩或语言异构，需拆包迁移（成本可控：包内已按层分目录）。
