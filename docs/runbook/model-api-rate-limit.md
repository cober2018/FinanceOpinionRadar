# LLM 限流（429）/配额耗尽

**现象**：extracting 卡住或 viewpoint 抽取 failed，error 含 429 / insufficient_quota。

**诊断**：`SELECT metadata_json->'llm_runs' FROM source_item WHERE id=<id>`；Job Center 页看 failed。

**恢复**：LLM Provider 自带指数退避重试（2 次）；配额耗尽需充值/换 key。恢复后对 failed 条目走「视频库→重驱」；extracting 状态条目需人工复位（见 worker-stuck）。

**会重复数据吗**：不会（ADR-0004 幂等键跳过已完成条目）。
**需要重跑吗**：失败 chunk 记录在 run 报告，可整条重抽。
