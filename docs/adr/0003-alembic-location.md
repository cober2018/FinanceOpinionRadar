# ADR-0003: Alembic 迁移目录置于 apps/api/migrations

日期：2026-09-16 ｜ 状态：已接受

## Context

执行计划树中 `migrations/` 在仓库根。迁移与 ORM 模型强耦合（autogenerate 依赖模型 import），而模型在 `apps/api/app/db/models/`。

## Decision

迁移放 `apps/api/migrations/`，`alembic.ini` 同级；从仓库根以 `alembic -c apps/api/alembic.ini` 调用（Makefile 已封装）。URL 不落 ini，运行时由 env.py 从 Settings 注入（显式覆盖 > .env/环境变量）。

## Consequences

- 迁移与模型同工程演进，单一 import 上下文。
- 根目录不出现迁移目录；CI/本地统一走 Makefile 目标。
