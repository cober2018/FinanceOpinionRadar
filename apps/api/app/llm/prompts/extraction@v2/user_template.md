以下是一篇财经视频的完整转录（共 __SEG_COUNT__ 段；每段格式 [seg:段号] 文本，段号从 __FIRST_SEGMENT_ID__ 起）：

__FULL_TEXT__

视频信息：标题「__TITLE__」，主播「__CREATOR__」

请通读全篇后按 schema 抽取观点：
- 不要逐段扫描式抽取；先建立全篇论述框架再输出
- claim：完整语义陈述（≤60 字）＝对象 + 期限 + 方向 + 核心理由
- stance：strong_bullish|bullish|neutral|bearish|strong_bearish|unclear
- horizon：intraday|1-3D|1-4W|1-3M|3M+；未提则省略
- 对象必须区分清楚：大盘 ≠ 某板块 ≠ 某个股；同一对象不同期限 = 多条；不同对象 = 多条；每条的 entities 填该条自己的对象（不是全篇出现过的所有对象）
- confidence 按校准给值；importance 按对投资决策的影响 0~1
- topic：主题短语（如「美联储利率」「半导体板块」）
- entities：观点针对的核心对象 [{"raw_name":"...","entity_type":"stock|index|commodity|institution|crypto|sector|other"}]——claim 里谈论的板块/指数/个股/商品/货币必须出现在这里（如「半导体」「纳斯达克」「黄金」「中证500」），这是观点的标的维度，不可省略
- conditional：是否带条件（「如果 X 那么 Y」）
- evidence_segment_ids：支撑该观点的段号（全篇范围内，可跨段）

反例（不要抽）：
- 「大家觉得呢？」——疑问
- 「有人说要涨」——转述他人
- 「如果跌破支撑位就要小心」——条件判断，抽时 conditional=true

只输出 JSON。
