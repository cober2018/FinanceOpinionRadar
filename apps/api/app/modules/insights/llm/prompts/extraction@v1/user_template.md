以下是一段财经内容的转录（segment_id 从 __FIRST_SEGMENT_ID__ 开始，时间戳毫秒）：

__CHUNK_TEXT__

请按 schema 抽取观点。要求：
- claim：一句可独立成立的观点陈述（≤80 字），保留方向与程度词
- stance：strong_bullish|bullish|neutral|bearish|strong_bearish|unclear 之一
- horizon：intraday|1-3D|1-4W|1-3M|3M+ 之一；未提时间维度则省略
- confidence：你对「这句话确实表达了该观点」的置信度 0~1
- importance：该观点对投资决策的重要性 0~1
- topic：所属主题的短语（如「美联储利率」「半导体板块」）
- entities：提到的具体标的/机构 [{"raw_name":"...","entity_type":"stock|index|commodity|institution|crypto|other"}]
- conditional：观点是否带条件（「如果 X 那么 Y」）
- evidence_segment_ids：支撑该观点的 segment id 列表（必须是输入的子集）

反例（不要抽）：
- 「大家觉得呢？」——疑问，无观点
- 「有人说要涨」——转述他人，未表态
- 「今天天气不错」——与财经观点无关

只输出 JSON。
