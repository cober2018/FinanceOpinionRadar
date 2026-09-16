# ADR-0006: AuditLog 写入服务推迟至 EPIC-05

日期：2026-09-16（Plan #1 eng review D42，用户批准）｜ 状态：已接受

## Context

PRD 13.15 要求所有人工修改落 audit_log。写入服务需要"人工审核操作"这一上游消费者（EPIC-05 的 review 界面/流程）才有真实调用方；提前实现只能写死调用点。

## Decision

V1 基线只建 `audit_log` 表结构（已在本计划迁移中落地）；write 服务（含 actor 鉴权、before/after 快照组装）推迟至 EPIC-05。

## Consequences

- EPIC-05 触发条件：审核界面落地、出现第一处人工修改操作时，必须先实现 audit 写入服务再开放修改入口。
- 在此之前表保持空置，无一致性风险。
