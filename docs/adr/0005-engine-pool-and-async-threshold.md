# ADR-0005: Engine 连接池参数与 sync→async 切换阈值

日期：2026-09-16（Plan #1 eng review D53/D59）｜ 状态：已接受

## Context

sync 引擎默认池参数未显式管理；ADR-0002 的 sync 决策需要量化退出条件，避免"永远 sync"或"过早重写"。

## Decision

- Settings 暴露 `db_pool_size=5`、`db_max_overflow=10`，engine 构建统一走 `app.db.session.get_engine()`（惰性缓存）。
- sync→async 切换触发条件（满足其一即启动 ADR-0002 复议）：
  1. API 读路径 p95 延迟因线程池排队持续劣化（观察 uvicorn 线程池等待指标）；
  2. 读 QPS 使池命中率接近上限（pool overflow 持续打满）；
  3. 出现长轮询/SSE 等天然异步需求。
  具体 RPS/并发阈值留空，上线后以实测基线填写。

## Consequences

- Follow-up 清单：多列索引上 naming convention 模板的表现；SourceAccount.failure_count 的重置策略；本 ADR 阈值量化；commit message 一致性抽查。
