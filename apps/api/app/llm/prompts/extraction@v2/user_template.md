以下是一篇财经视频的完整转录（共 __SEG_COUNT__ 段；每段格式 [seg:段号] 文本，段号从 __FIRST_SEGMENT_ID__ 起）：

__FULL_TEXT__

视频信息：标题「__TITLE__」，主播「__CREATOR__」

请通读全篇后按 schema 抽取观点：
- 不要逐段扫描式抽取；先建立全篇论述框架再输出
- claim：完整语义陈述（≤60 字）＝对象 + 期限 + 方向 + 核心理由
- stance：strong_bullish|bullish|neutral|bearish|strong_bearish|unclear
- horizon：intraday|1-3D|1-4W|1-3M|3M+；未提则省略
- 对象必须区分清楚：大盘 ≠ 某板块 ≠ 某个股；同一对象不同期限 = 多条；不同对象 = 多条
- confidence 按校准给值；importance 按对投资决策的影响 0~1
- topic：主题短语（如「美联储利率」「半导体板块」）
- entities：具体标的 [{"raw_name":"...","entity_type":"stock|index|commodity|institution|crypto|other"}]
- conditional：是否带条件（「如果 X 那么 Y」）
- evidence_segment_ids：支撑该观点的段号（全篇范围内，可跨段）

反例（不要抽）：
- 「大家觉得呢？」——疑问
- 「有人说要涨」——转述他人
- 「如果跌破支撑位就要小心」——条件判断，抽时 conditional=true

只输出 JSON。
