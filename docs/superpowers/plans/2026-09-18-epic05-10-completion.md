# EPIC-05~10 剩余开发总计划（2026-09-18）

上游：执行计划 §8~§13（RAD-050~105），开发者清单第 7~12 步。
前置已就绪：EPIC-04 抽取链（candidate viewpoints + 证据绑定）、audit_log 表、
consensus 两表、job_run 表、状态机 extracting→reviewing→ready。

## 顺序与依赖

1. **EPIC-05 审核流**（RAD-050~052）——依赖 EPIC-04 candidate
2. **EPIC-06 历史与共识**（RAD-060~062）——依赖 confirmed 视角
3. **EPIC-07 API**（RAD-070~072）——依赖数据面
4. **EPIC-08 前端补全**（RAD-080~089）——消费 07 的 API；已有四页映射见 D8
5. **EPIC-09 质量评估**（RAD-090~092）——CLI + golden 脚手架（20 条人工标注需用户参与）
6. **EPIC-10 运维**（RAD-100~105）——compose/队列/重试/日志/备份/runbook

## 每 EPIC 关键决策

### EPIC-05（D5.x）
- D5.1 Reviewer Agent V1 = **规则 agent**（无 LLM 依赖）：quoted 转述启发式、证据充分性
  （条数+总字数阈值）、confidence/stance 闸 → accept/reject/needs_review + 修正建议。
  LLM reviewer 留 EPIC-05+（provider 已就绪，接入是配置活）。
- D5.2 入队条件可配置：settings `review_confidence_threshold`(0.75)、
  `review_min_evidence_chars`(50)。抽取完成即跑 reviewer：accept→confirmed，
  其余→needs_review/rejected。
- D5.3 Review API：confirm/reject/PATCH 每次写 audit_log（actor/action/before/after/reason）；
  confirm 触发 RAD-060 快照；item 无剩余 candidate 时 reviewing→ready。

### EPIC-06（D6.x）
- D6.1 change_type 规则（unit test 全覆盖）：prev None→new_thesis；stance 变→stance_flip；
  stance 同强度变→strengthening/weakening；仅 horizon 变→horizon_change；全同→repeated；
  unclear 参与比较但 prev unclear → new_thesis。
- D6.2 Snapshot：upsert (creator,topic,snapshot_date=viewpoint.as_of_date||today)。
- D6.3 Consensus：每 topic 每日每 creator 取最新 confirmed 未过期非 unclear 视角；
  counts/ratio/net=(bull-bear)/total/disagreement=1-|net|；规则版本常量 CONSENSUS_RULE_VERSION="v1"。
- D6.4 Expiry：horizon→有效期映射（intraday 1 / 1-3D 3 / 1-4W 28 / 1-3M 90 / 3M+ 180 可配），
  过期 snapshot.change_type='expired'（不删数据）；beat 每日 + 手动端点。

### EPIC-07（D7.x）
- D7.1 关键列表/详情端点补 response_model（Pydantic schema）；`make api-types` 生成 TS 类型。
- D7.2 `GET /dashboard?date=`：单请求聚合（统计卡/共识表/直播间/最近观点）。
- D7.3 `GET /viewpoints`：page/page_size/sort/date_from/date_to/creator_id/topic_id/
  stance/confidence_min/status → {items,total,page,page_size}。

### EPIC-08（D8.x）——已有四页映射
- 已具备：tokens(index.css)=RAD-080（迁移到 styles/tokens.css）、App Shell、Dashboard 雏形、
  证据抽屉雏形、来源中心（账号 CRUD）。
- 补齐：RAD-083 观点 DataTable 页（筛选/排序/分页/URL query）；RAD-084 抽屉补 seek 链接与
  review 动作；RAD-085 人物详情（header+topic 态+时间线）；RAD-086 主题共识（KPI+分布）；
  RAD-088 Review Queue（键盘 A/R/E + reject 二次确认）；RAD-089 Job Center（读 job_run）。

### EPIC-09（D9.x）
- D9.1 golden 目录脚手架 + 2 条样例标注（完整 20 条人工标注需用户执行，CLI 即刻可用）。
- D9.2 `scripts/evaluate_viewpoints.py`：六指标（extraction_precision/recall/stance_accuracy/
  entity_accuracy/evidence_coverage/schema_pass_rate）+ JSON/Markdown 报告。
- D9.3 门限文件 `tests/golden/thresholds.json`；回归对比 `--baseline`。

### EPIC-10（D10.x）
- D10.1 `infra/docker/docker-compose.prod.yml`：web/api/worker(default|media|llm)/pg/redis。
- D10.2 队列路由：default/media/llm 队列拆分（task_routes），compose 分 worker。
- D10.3 重试策略集中（LLM 任务 autoretry_on=(LLMError,) 重试 2；风控/认证类不重试）。
- D10.4 `docs/runbook/` 7 篇（现象/诊断/恢复/重复数据/重跑）。
- D10.5 `scripts/backup_db.sh` + 恢复演练文档。

## 明示不做（V1 边界）

Playwright 截图基线自动化、GPU worker 主机、Helm、真实标注 20 条（需用户）、多用户权限。
