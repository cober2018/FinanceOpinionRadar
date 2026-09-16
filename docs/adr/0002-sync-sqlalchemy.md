# ADR-0002: SQLAlchemy 使用 sync（非 async）

日期：2026-09-16 ｜ 状态：已接受

## Context

FastAPI 支持 async 路由 + asyncpg/psycopg async。但 V1 的写路径在 Celery worker（同步模型），读路径为低并发内部查询；两套 session/repo 代码会双倍维护面。

## Decision

SQLAlchemy 2.x sync + psycopg（同步驱动）。FastAPI 的 def 路由在线程池执行，不阻塞事件循环；worker 与 API 共用同一套 repository。

## Consequences

- 单套 repo 代码；实现简单，调试栈直白。
- 读并发上限受线程池约束；切换阈值与触发条件见 ADR-0005。
