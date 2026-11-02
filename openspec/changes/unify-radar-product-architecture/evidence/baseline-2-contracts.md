# 基线冻结 2：契约与兼容基线（任务 1.2）

冻结时刻：2026-11-03，基线 main `b013bfe`。重构必须保持以下契约不变；本清单即切换前核对清单。

## 1. 对外 API（路径/鉴权/行为不变）

- 控制台 `/api/v1/*`（`apps/api/app/api/v1/__init__.py` 装配序）：source_items、source_accounts、monitoring、transcripts、viewpoints、taxonomy、open_admin、danmaku、retention、proxy_pool、timeline。
- 开放接口 `/open/v1/*`（`api/open/routes.py` + `deps.py` 鉴权/审计中间件）：viewpoints、viewpoints/{id}、items/{id}/transcript、items/{id}/summary、video-summaries（游标增量 ready/withdrawn）、broadcasters/recent-summaries、transcripts/search、creators、broadcasters、entities。Key 鉴权 `X-API-Key`（active 有效；disabled 暂停；revoked 不可恢复）。
- 健康：`GET /api/v1/health`。静态：`/`（landing）、`/console/*`（web dist）。
- 事件载荷：`vsum-<id>` + payload（version/state ready|withdrawn、summary、generated_at、updated_at、time 语义字段）；Webhook 签名 HMAC-SHA256。

## 2. Celery 任务名/队列/beat（R3 兼容面，旧消息必须可被新版本消费）

任务路由（`worker/celery_app.py`）：`prepare_source_item`→media、`prepare_live_segment`→media、`extract_source_item_viewpoints`→llm、`discover_source_account`→media、`collect_danmaku`→danmaku（≤12h 长任务独立队列）；其余 sweep 在 default。beat_schedule 14 项：dispatch-due-discoveries、dispatch-pending-prepares、ingest-live-segments、sync-live-monitors、dispatch-pending-extractions、retry-failed-prepares（900s）、dispatch-danmaku-collectors、ingest-danmaku-files、retention-sweep、qg-proxy-maintain（600s）、push-confirmed-viewpoints、push-reviewed-video-summaries、orphan-media-sweep、live-segment-retention、danmaku-sink-retention、housekeeping（3600s）。broker/backend=redis（`redis_url`）。

## 3. 数据归属与跨模块事务（R1 对应现状）

- 表结构 25 张应用表 + alembic 迁移链不改不重建；模型 `db/models/`：source（SourceItem/SourceAccount）、media、viewpoint、creator、consensus、interaction、open_api（key/channel/event/delivery）、taxonomy、system（JobRun 等）。
- 事件事务（`services/video_summary_feed.py`）：`withdraw_if_invalid`（L153-175）在来源/审核变更事务提交前撤回，锁序 `_lock_source_item → _lock_event_order`；`record_ready_summary`（L178-204）同事务核对指纹（`expected_fingerprint`）+资格后写 ready 事件；`erase_deleted_item_history` 删除留痕。总结保存与 ready 事件同事务；模型调用在事务外。
- 来源变化联动：`live_ingest.py:472-482` 标题/起播变化 → `withdraw_if_invalid`。
- 推送状态（`video_summary_push.py` L97-176）：sent/failed/dead/unknown 计数；`status in (sent,dead,unknown)` 不重发；网络未知→`unknown` 人工对账；非 2xx 有限重试→`dead`。逐观点推送 `PushDelivery`（channel×viewpoint）保持独立。

## 4. 持续运行语义（R2 对应现状）

- 直播分片（`live_ingest.py`）：分片幂等键 `metadata_json.live.processed[str(index)]`（L556-558 重跑跳出）；未完成/写入中文件等待稳定；`segment_errors/deferred/skipped_segments/segment_paths` 注册表；转录累计偏移；起播=注册表最早分片（11:33 不得被子场 11:47 覆写，L440-441 实录）；条目时长=全分片时长加总。
- 弹幕（`danmaku/collector.py`）：单飞 pg advisory lock `907300000+item_id`（连接断开自愈）；sink envelope v1 `{v, received_at, msg|raw}` 追加写+flush；心跳 `os.utime`；退出 `session_closed/max_duration/room_not_found`；协议重连 15s 在采集循环内（F6）；全程零 DB 写（避开 metadata_json 整列覆盖竞态）。观众弹幕与主播转录分离，ingest 幂等。
- 值守桥（`recorder_bridge.py`）：diff 无变化不扰动（防重启风暴实录：字符串化布尔曾致每 10 分钟 restart 丢音频）；`user_settings.json` 只补缺不覆盖；http→https 房间 URL 归一；主页 URL 不当直播间。

## 5. 文件/存储路径（存量引用必须保持可读）

- 分片：`LIVE_SEGMENTS_DIR`（bind /app/downloads）`<platform>/<author>/<date>/`；恢复路径写进业务元数据 `segment_paths`（L574）。
- 弹幕档案：`DANMAKU_SINK_DIR/<YYYY-MM-DD>/<item_id>.jsonl`。
- 音频/存储：`services/storage/s3.py`（MinIO/S3）；storage URI 在 DB（orphan_sweep 对账）。
- 时间口径：视频=平台发布时间；直播=最早分片开播时刻（`published_at` 统一时间戳，用户 2026-09-26 确认）；分片名前 14 位本机时区 YYYYMMDDHHMMSS。

## 6. 现有检查基线

- `pytest.ini` + `tests/{unit,integration}`（隔离测试库，历史 53 项相关检查通过）；前端 vitest 1 个测试文件（App.test.tsx）；lint：ruff（后端）+ eslint（前端，存在未改处既有警告）；`make doctor/lint/test/build-web`。工程入口 Makefile + scripts/dev_up.sh（Postgres/Redis/dtk/API:8010/worker/CPA 隧道）。
- 迁移后必须重跑并保持通过（只同步入口/引用，不改断言）。

## 7. 隔离演练输入（任务 4.2 用）

- 存量副本：本地 `data/`（分片、弹幕 sink、DB dump 构造）或人为构造等价记录；隔离 DB/Redis/媒体目录/接收端（本地 HTTP 回环）；禁止生产队列与真实订阅方。
