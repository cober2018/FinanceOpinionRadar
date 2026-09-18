# Golden Dataset（EPIC-09，RAD-090）

人工标注集：**20 个视频**的转录 + 人工标注观点，用于抽取质量评估与 prompt 回归门禁。

目录结构：
- `sources.json` — 视频来源清单（platform/url/item_id）
- `transcripts/*.json` — 每视频的 transcript_segment 列表
- `annotations/*.json` — 人工标注观点（claim/stance/horizon/entities/evidence_segment_ids）
- `expected/` — 评估期望产物（由 CLI 生成对照）
- `thresholds.json` — 回归门限（RAD-092）

标注节奏：当前 2 条样例已就位（可跑通 CLI）；剩余 18 条由人工按 samples 格式补充。
