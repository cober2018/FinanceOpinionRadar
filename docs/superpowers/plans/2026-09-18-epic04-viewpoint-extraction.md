# EPIC-04 观点抽取实施计划（Chunk 与 LLM 观点抽取）

日期：2026-09-18。上游：执行计划 §7（RAD-040~045），开发者清单第 6 步。
前置全部就绪：transcript 落库链路（EPIC-03/Plan #4）、viewpoint/entity/topic 表（RAD-012）、
状态机 `transcribed→extracting→reviewing`（pipeline_states 预留）、ADR-0004 幂等键约定。

## 决策

- **D1 派发语义沿转录**：beat 补扫 `transcribed` 且非 backfill、账号 auto_poll 的条目派发
  `extract_source_item_viewpoints`；视频库加「抽取观点」手动按钮（同手动转写模式）。
  LLM token 是钱，杜绝 backfill 全量抽取。
- **D2 任务粒度**：`extract_source_item_viewpoints(item_id)` 单任务内分 chunk 串行
  （chunk 间无顺序依赖但共享 run 上下文；per-chunk 任务留 EPIC-05 审核流再拆）。
- **D3 幂等（ADR-0004）**：抽取前查 `(source_item_id, prompt_version, extractor_version)`
  已有 viewpoint → 跳过。extractor_version = `v1`，prompt_version = `extraction@v1`。
- **D4 LLM Provider**：OpenAI-compatible `/chat/completions`（`response_format=json_object`），
  超时+指数重试+usage 记录；`LLM_API_KEY` 空 → MockProvider（返回合法空抽取），
  dev 无钥可跑通全链。schema 校验在服务端，不信任模型自证。
- **D5 原始输出**：`object://llm-runs/{run_id}.json`（MinIO，RAD-030 存储服务），
  run 索引记入 `source_item.metadata_json["llm_runs"]`。
- **D6 实体归一**：V1 确定性四阶（canonical → alias → symbol → unique fuzzy），
  唯一命中→绑定；歧义/未命中→`entity_candidate` 表（新迁移），LLM 消歧留 EPIC-05。
- **D7 去重**：同 source_item 内规则合并（entity+stance 相同且 claim 序列相似度 ≥0.85），
  保留 merge_reason；LLM reviewer 留 EPIC-05。
- **D8 服务端校验（RAD-043 全清单）**：evidence_segment_ids ⊆ chunk、stance/horizon 枚举、
  confidence∈[0,1]、claim 非空；无有效证据 → 拒绝该 candidate（计数进 run 报告）。

## 任务（RED→GREEN）

1. RAD-040 `app/domain/transcript/chunker.py`：纯函数 chunk_segments(segments,
   target_ms=7.5min, max_ms=10min, overlap_segments=1)。单测：边界/不切段/overlap 标记。
2. RAD-042 `app/llm/provider.py`：`generate_json(system, user, schema, model, temperature)`；
   Mock + OpenAI 兼容实现；settings `llm_model/llm_timeout_sec/llm_max_retries`。单测（打桩 httpx）。
3. RAD-041 `app/llm/prompts/extraction@v1/`：system/schema/说明/反例 + PromptRegistry
   （按版本目录加载，不可变）。单测：版本加载与缺失报错。
4. RAD-043 `app/services/extraction.py` + `extract_source_item_viewpoints` 任务 +
   `dispatch_pending_extractions` beat + 手动端点。集成测试：候选落库/证据绑定/校验拒绝/
   幂等跳过/状态推进/原始 run 落存储。
5. RAD-044 `app/services/entity_normalizer.py` + `entity_candidate` 迁移。单测四阶匹配与歧义入候选。
6. RAD-045 `app/services/viewpoint_dedupe.py`。单测：相似合并 + merge_reason。
7. 门禁 + commit（每 2-3 任务一笔）。

## 明示不做（EPIC-05+）

LLM 消歧与去重 reviewer、审核流 UI、embedding 召回、per-chunk 任务拆分、topic 自动扩充审核。
