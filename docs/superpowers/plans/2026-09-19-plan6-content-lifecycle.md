# 内容生命周期与精华资产库实施计划（Plan #6：TTL 自动清理 + 结论留存）

> **本仓库既定约定（沿 Plan #1–#5）**：主会话逐任务串行执行；单分支 main 直推；提交规范 `feat|fix|docs|chore`；TDD；全量 `pytest + ruff + mypy` 后 commit。

**Goal:** 非精华内容到期物理删除（转录/媒体/弹幕级联清空），删除前把该条目的观点结论快照进 `content_summary`（将来内容中心生成文章的数据源之一）；被标记"精华资产"的条目永久保留。数据量随 TTL 自动收敛，不日积月累。

**Architecture:** 纯 radar 侧能力，零外部依赖。`retention` 服务（beat 每日 + 手动 API）：扫到期条目 → 结论快照 → 墓碑（防 discover 重导，复用手工删除语义）→ 级联物理删 → 删 jsonl 档案。精华标记 `source_item.is_asset`。

**Tech Stack:** 既有 alembic/celery beat/SQLAlchemy 全复用；新表 1 张（content_summary）、新列 2 个（is_asset/asset_at）。

---

## 0. 用户需求原话（2026-09-19）

> 数据日积月累一天几百条增量太大；有深度的、经典的、精华的视频放进一个资产库入库；非精华的定期物理删除，只留一个结论，作为内容中心生成文章的数据源之一；未进资产库的内容设置生命周期自动清除。

## 1. 设计决策（冻结）

- **D1 清理范围**：`status IN ('transcribed','failed','reviewing','ready')` 且 `is_asset=false` 且 `created_at < now - content_retention_days`。`discovered/resolved/media_ready`（体量小、可能待转写）与 `transcribing`（直播进行中）、`extracting`（抽取链进行中）一律跳过，下轮再清。
- **D2 TTL 起算 = item.created_at**（会话/条目建立时间），不新增完成时间列（转写当天完成是常态，误差可接受；README 写明）。`content_retention_days` 默认 30，**0 = 禁用清理**。
- **D3 结论留存 = content_summary 快照**（不改 viewpoint 链）：`Viewpoint.source_item_id` 是 CASCADE，清理时先读 item 全部观点打包成一行 summary_json（claim/stance/confidence/importance/as_of_date/verification_status），再删 item。无观点条目也留一条（viewpoints=[]，标题/主播/日期留档）。内容中心按 creator_name + 时间轴消费。
- **D4 墓碑必写**：sweep 复用手工删除语义，逐条写 `DeletedItemRef`（撞已有墓碑则跳过写入），否则 discover 下一轮重导同一视频，清理变死循环。
- **D5 磁盘文件**：jsonl 弹幕档案（`data/douyin/danmaku/*/<item_id>.jsonl`）随 item 删除；StreamCap TS 分片体积大但 v1 不自动删（误删风险，挂账 V2，README 给手动清理命令）。
- **D6 精华 = 人工标记**：v1 无自动评分；UI 星标 + `POST /source-items/{id}/asset`。自动打分挂账。
- **D7 展示**：列表响应带 `is_asset` 与 `expires_at`（asset → null；清理范围内条目 → created_at + retention）；前端星标 + 筛选 + "N 天后清理"小字。总览不加卡。

## 文件结构

```text
apps/api/migrations/versions/f7a8b9c0d1e2_content_lifecycle.py   # is_asset/asset_at + content_summary 表
apps/api/app/db/models/interaction.py                            # +ContentSummary；source.py +is_asset/asset_at
apps/api/app/core/settings.py                                    # +content_retention_days / retention_sweep_interval_sec
apps/api/app/services/retention.py                               # sweep_expired_content（快照→墓碑→级联删→删jsonl）
apps/api/app/repositories/content_summaries.py                   # 快照写入
apps/api/app/api/v1/retention.py                                 # POST /retention/sweep（dry_run）
apps/api/app/api/v1/source_items.py                              # POST /{id}/asset + list 带is_asset/expires_at + asset 筛选
apps/api/app/worker/tasks.py / celery_app.py                     # sweep_content_retention + beat 每日
tests/unit/test_retention.py（纯逻辑）/ tests/integration/test_retention_sweep.py（真库全链）
tests/integration/test_danmaku_api.py 扩展（asset toggle/筛选）
infra：.env.example / README「内容生命周期」小节
```

### content_summary 表

```text
id, platform String(30), creator_name String(200), item_title Text,
item_type String(20), item_created_at TIMESTAMPTZ, item_published_at TIMESTAMPTZ NULL,
viewpoints JSONB (list[{claim,stance,confidence,importance,as_of_date,verification_status}]),
created_at/updated_at, id BigInt PK
INDEX (creator_name, item_created_at)
```

## Tasks

- [ ] Task 1 迁移+模型（TDD）：integration 迁移后可读写 is_asset；content_summary 落库
- [ ] Task 2 retention 服务（TDD）：integration——过期 transcribed 删+快照+墓碑+jsonl 删；未过期留；is_asset 留；failed/reviewing/ready 清；discovered/transcribing/extracting 不清；dry_run 零删除；墓碑撞车安全
- [ ] Task 3 API：asset toggle（404/幂等）+ 列表 is_asset/expires_at/asset 筛选 + POST /retention/sweep；单测
- [ ] Task 4 beat 接线 + 注册表断言
- [ ] Task 5 前端：星标 toggle、精华筛选、到期小字；vitest + build
- [ ] Task 6 README/.env.example + 全量门禁 + 提交

## 风险与挂账

| 风险 | 处置 |
|---|---|
| 误删精华 | is_asset 手动闸 + 默认 30 天缓冲 + dry_run 预览 API |
| 观点抽取进行中被打断 | extracting 状态跳过（D1） |
| discover 重导 | 墓碑必写（D4） |
| TS 分片磁盘增长 | v1 不自动删，README 手动命令；挂账 V2 |
| 自动精华评分 | 挂账（LLM 评分后置） |
