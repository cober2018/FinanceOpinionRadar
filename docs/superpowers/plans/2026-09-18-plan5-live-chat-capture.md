# 直播弹幕采集实施计划（Plan #5：抖音直播间留言 → 舆论分析语料）

> **本仓库既定约定（沿 Plan #1–#4）**：主会话逐任务串行执行；单分支 main 直推；提交规范 `feat|fix|refactor|docs|chore`；TDD（先红后绿）；全量 `pytest + ruff + mypy` 后 commit。

**Goal:** 抖音直播间弹幕（观众侧语料）自动采集入库：开播会话期间实时连外部 douyinLive 服务收 WS 弹幕流 → jsonl 落盘 → beat 扫描幂等入 `live_chat_message` 表，为 EPIC-04+ 观众情绪分析供数。

**Architecture:** 签名战争（a_bogus/webcast wss）继续隔离在核心域之外——第三个外部容器 `jwwsjlm/douyinLive`（Go，本地签名免浏览器，断线重连/未开播轮询/心跳保活全由它做）。radar 侧只做三件事：beat 派发器（找进行中的 live 会话 → 派采集任务）、采集 worker（WS 客户端 → 追加 jsonl）、ingest（扫 jsonl → 幂等入库）。与 StreamCap 分片链路同构：文件解耦、可独立验收。

**Tech Stack:** `websocket-client`（同步 WS，新增依赖）；celery/beat/PG 全复用；外部服务：`ghcr.io/jwwsjlm/douyinlive:v2.2.1`（pin tag，沿 DX-7）。

---

## 0. 调研结论（2026-09-18，已冻结）

全网调研（GitHub/知乎/CSDN/V2EX 等）结论 + 源码级协议核实：

- **选定主线路**：`jwwsjlm/douyinLive`（★465 · Go · 2026-09-17 仍在更新）。官方 Docker 镜像、本地 a_bogus 签名（免浏览器）、`ws://127.0.0.1:1088/ws/{room_id}` 订阅式 WS 代理 + 只读 HTTP API（`/api/v1/rooms/{id}/status`）。上游断线重连/未开播轮询/验证码指纹轮换全在其内部。
- **协议事实（源码 room_session.go + new_douyin.proto 核实）**：
  - 系统消息：`{type:"system", event:"live_status", code: ROOM_ONLINE|ROOM_OFFLINE|ROOM_ENDED|ROOM_NOT_FOUND|ACCOUNT_OFFLINE_NO_ROOM|ROOM_STATUS_UNKNOWN, live, live_name, title, room_id, ...}`。未开播时服务端保持连接并轮询，开播自动推送 ROOM_ONLINE 并开始转发；`ROOM_NOT_FOUND` 时服务端主动断开客户端。
  - 业务消息 = protobuf protojson（camelCase）+ 顶层注入 `method/livename/title/avatarThumb`。
  - **protojson 的 uint64 一律是字符串**（`common.msgId`/`createTime`/`gift.repeatCount` 等都是 `"123"`），解析器必须做 string|int 双兼容。
  - 常用字段：ChatMessage `{common:{msgId,createTime,describe}, user:{id,shortId,nickname}, content}`；GiftMessage `{user, gift:{name}, repeatCount, count}`；LikeMessage `{user, count, total}`；MemberMessage `{user, memberCount, actionDescription}`；SocialMessage `{user, shareType, action}`。
  - 客户端保活：每 30s 发文本 `"ping"`，服务端回 `"pong"`。
- **排除项**：官方开放平台"直播玩法"需主播侧授权（不适合监控第三方）；StreamCap 无弹幕能力（源码 Recording 模型无该字段，网传信息不实）；DouyinBarrageGrab 系统代理方案需 Windows 有头环境。
- **备选（用户已裁决：当天不行再换，不预建）**：纯 Python 路线 saermart/DouyinLiveWebFetcher；CDP 浏览器路线 ymstar/dyhub。

## 1. 设计决策（F 系编号，实施时不再讨论）

- **F1 采集窗口绑定录制会话**：派发器只为 `item_type='live' AND status='transcribing'` 的会话派采集任务（即 StreamCap 已落首个分片之后）。弹幕数据天然归属同一场直播的会话行，douyinLive 连接数受实际直播数约束。代价：开播后首分片落盘前（~5-10min）的弹幕不采集——挂账 V2（always-on 采集器）。
- **F2 防重双层闸**：硬闸 = pg advisory lock（per-item key，采集任务入口 `pg_try_advisory_lock`，连接断开自动释放——崩溃即解锁）；软闸 = sink 文件 mtime 新鲜度（采集器每 30s touch 文件；派发器只对 mtime 过期（>120s）或文件不存在的会话派发）。**采集器全程零 DB 写**——彻底避开与 live_ingest 对 `metadata_json` 整列 JSONB 覆盖的竞态（两写者 clobber 风险是 Plan #4 已知坑的放大）。
- **F3 文件即原始档案**：jsonl 每行 `{"v":1,"received_at":ISO,"msg":<服务端原文>}`；DB 只存解析后的类型化列（不存 raw_json——单场 10 万级消息 × KB 级嵌套 user 对象会把库撑爆）。jsonl 与 StreamCap 分片同级保留，可随时重放重解析。
- **F4 落盘布局**：`<DANMAKU_SINK_DIR>/<YYYY-MM-DD>/<item_id>.jsonl`。日期取会话键 `live:{external_id}:{date}` 的 date 段（会话身份决定，不受跨零点影响——同一会话单文件追加）。文件名由 radar 自己生成，ingest 侧只认 `^\d+\.jsonl$`（F7 注入面同理：文件名不进任何下游路径拼接）。
- **F5 幂等键**：`(source_item_id, external_msg_id)` 唯一约束，`INSERT ... ON CONFLICT DO NOTHING`。`external_msg_id` 优先 `common.msgId`；缺失时用 `method:createTime:userId:hash8(text)` 确定性合成（重放稳定）。采集器崩溃重派 → 追加重复行 → 入库自动去重。
- **F6 退出条件**：会话不再 transcribing（live_ingest 收尾/失败）、服务端 ROOM_NOT_FOUND、超过 `danmaku_collector_max_duration_sec`（默认 12h）、WS 连接失败持续到会话结束（重连间隔 15s，不设次数上限——会话生命周期天然有界）。
- **F7 队列隔离**：采集任务路由到新队列 `danmaku`（12h 级长任务不占 media/llm）；`make worker-beat` 的 `-Q` 列表同步加 `danmaku`。
- **F8 优雅降级**：`danmaku_ws_base_url` 或 `danmaku_sink_dir` 为空 → 派发/ingest beat 每轮单行 no-op 日志（DX-5 模式）；douyinLive 容器不可达 → 采集器 warning 重试，不影响分片链路。
- **F9 明示不做（V1 边界）**：弹幕的 LLM 情绪分析（EPIC-04+，本计划只到"语料入库"）；前端展示/API（psql 验收）；短视频评论采集（下一计划）；always-on 采集器（F1 挂账）；水军/去重清洗。

## 文件结构（新增/修改）

```text
apps/api/app/services/danmaku/parse.py          # WS 消息解析（系统/业务分流、类型化抽取、合成 id）
apps/api/app/services/danmaku/collector.py      # 采集 worker（advisory lock + WS 循环 + jsonl 追加 + mtime touch）
apps/api/app/services/danmaku/dispatch.py       # beat 派发器（候选会话查询 + mtime 新鲜度 + 并发上限）
apps/api/app/services/danmaku/ingest.py         # beat ingest（扫 jsonl → 幂等入库 → 计数行）
apps/api/app/services/danmaku/__init__.py
apps/api/app/services/recorder_bridge.py        # +resolve_room_id(account) 公共助手（复用 resolve_room_url）
apps/api/app/repositories/live_chat_messages.py # insert_many（ON CONFLICT DO NOTHING）
apps/api/app/db/models/interaction.py           # LiveChatMessage 模型
apps/api/app/db/models/__init__.py              # 导出
apps/api/app/worker/tasks.py                    # +collect/dispatch/ingest 三个任务壳
apps/api/app/worker/celery_app.py               # +danmaku 队列路由 + 2 个 beat 条目
apps/api/app/core/settings.py                   # +7 个 danmaku settings（空=禁用）
apps/api/app/migrations/versions/xxxx_live_chat_message.py
apps/api/pyproject.toml                         # +websocket-client
tests/unit/test_danmaku_parse.py / test_danmaku_collector.py
tests/unit/test_danmaku_dispatch.py / test_danmaku_ingest.py
tests/integration/test_live_chat_message_model.py
tests/unit/test_worker_task_registry.py         # +注册表断言
infra/docker/docker-compose.douyin.yml          # +douyinlive 服务（pin v2.2.1）
Makefile                                        # worker-beat -Q 加 danmaku
.env.example / README.md / 02_..._execution_plan.md（注记⑥）
```

### Settings 新增（全部有默认值，空 = 禁用）

```python
danmaku_ws_base_url: str = ""               # ws://127.0.0.1:1088；空=派发/采集不启用
danmaku_sink_dir: str = ""                  # jsonl 根目录（建议 data/douyin/danmaku）
danmaku_dispatch_interval_sec: int = 60     # beat：派发器
danmaku_ingest_interval_sec: int = 120      # beat：ingest 扫描
danmaku_collector_max_duration_sec: int = 43200   # 单采集任务寿命上限（12h）
danmaku_heartbeat_stale_sec: int = 120      # sink mtime 超过此值视为采集器不在
danmaku_max_collectors: int = 4             # 并发采集任务上限
```

### DB：live_chat_message 表

```text
id, source_item_id FK(source_item ON DELETE CASCADE), platform('douyin'),
msg_type String(60)            -- WebcastChatMessage 等原始 method
external_msg_id String(64)     -- common.msgId / 确定性合成
user_id String(64) NULL, user_name String(200) NULL,
text Text NULL, gift_name String(200) NULL,
repeat_count Int NULL, like_count Int NULL, member_count Int NULL,
published_at TIMESTAMPTZ NULL  -- common.createTime（秒；>1e12 按 ms 归一）
received_at TIMESTAMPTZ NOT NULL, created_at/updated_at
UNIQUE (source_item_id, external_msg_id)
INDEX (source_item_id, published_at)
```

---

## Task 1: 选型 spike（真栈验证，timebox 30min）

- [x] `docker run ghcr.io/jwwsjlm/douyinlive:v2.2.1` 起容器，`curl 127.0.0.1:1088/api/v1/health` 探活
- [x] `websocat`/python 一行脚本连 `ws://127.0.0.1:1088/ws/<测试房间号>`，实录一条系统消息 + （若在播）一条业务消息样本 → 存 `tests/fixtures/danmaku/sample_*.json` 作解析器 fixture（沿 Plan #4 "fixture 必须实录" 教训）
- [x] 决策门：容器起不来/房间号全 404 → 停下报告用户（备选方案当天裁决，不自行切换）

### Task 1 决策记录（2026-09-18 实录）

- **容器真栈通过**：`ghcr.io/jwwsjlm/douyinlive:v2.2.1`（commit 60823bae，2026-08-23 构建）`/api/v1/health` OK，`sign_provider=local`（本地签名生效，无需浏览器/TikHub）。
- **HTTP API 实录**：`GET /api/v1/rooms/330698468897/status` → `{"status":"account_no_room","has_room":false,"is_live":false,"live_name":null}`（新闻联播 23:53 已收播，状态语义与文档一致）。
- **WS 实录**：连 `ws://127.0.0.1:1088/ws/330698468897` 秒开，收到完整系统消息 `ACCOUNT_OFFLINE_NO_ROOM`（字段与文档逐项一致，含 `live_name=新闻联播`/`avatar_thumb`/`retry_interval_seconds`）→ 存 `tests/fixtures/danmaku/sample_system_offline.json`。
- **业务消息实录未获**（决策门不触发）：4 个值守房间（含 大潘说股）spike 时段全部 offline（深夜，股市直播收播）。业务消息序列化路径已从源码核实（`room_session.go`：protojson Marshal + 顶层注入 `method/livename/title/avatarThumb`；proto 字段见 `new_douyin.proto` ChatMessage/GiftMessage/LikeMessage/MemberMessage/SocialMessage），fixture 按 proto schema 合成（camelCase + uint64 字符串形态），**真实弹幕样本冒烟挂 Task 5**（值守房间下一开播窗口，交易时段必有）。
- **部署形态**：`docker run -d --name douyinlive --restart unless-stopped -p 127.0.0.1:1088:1088 ghcr.io/jwwsjlm/douyinlive:v2.2.1` 已在宿主运行（Task 2 转正进 compose）。

## Task 2: 模型 + 迁移 + settings + compose（TDD）

- [ ] RED：integration `test_live_chat_message_model.py`——迁移后表存在、唯一约束拒重复行、级联删除随 item
- [ ] GREEN：模型/迁移（沿 b7e4d2c9a5f1 风格）；settings +7 键；`docker-compose.douyin.yml` +douyinlive 服务（`127.0.0.1:${DOUYINLIVE_PORT:-1088}:1088`，pin v2.2.1，restart unless-stopped）；pyproject +websocket-client；Makefile -Q 加 danmaku
- [ ] 全量 pytest + ruff + mypy；commit `feat: live_chat_message 表与弹幕采集配置基建`

## Task 3: 解析器 + 采集器（TDD）

- [ ] RED `test_danmaku_parse.py`：系统/业务分流；chat/gift/like/member/social 五类类型化抽取；uint64 字符串形态；msgId 缺失合成 id 的确定性（同消息两次解析同 id）；createTime 秒/毫秒双形态；恶意/缺字段消息不抛异常（chaos）
- [ ] RED `test_danmaku_collector.py`（fake WS，零网络）：
  - 正常流：fake WS 吐系统消息 + 3 条业务消息 → jsonl 3 行 envelope、mtime touch 被调
  - 锁拒绝：advisory lock 抢不到 → 立即返回 skipped
  - 会话收尾：item status 变 transcribed → 循环退出
  - ROOM_NOT_FOUND → 退出且记原因
  - WS 断开 → 重连（fake 工厂计数）直到会话关闭
  - 未配置（ws_base_url 空）→ no-op
- [ ] GREEN：实现 parse.py + collector.py（连接工厂/时钟/sleep 全部可注入）；commit `feat: 弹幕采集 worker（WS 客户端/jsonl 落盘/advisory lock 防重）`

## Task 4: 派发器 + ingest + beat 接线（TDD）

- [ ] RED `test_danmaku_dispatch.py`：候选 = douyin + enabled + live_monitor_enabled + transcribing live 会话 + 可解析 room_id；mtime 新鲜 → 不派发；过期/无文件 → 派发；活跃数 ≥ max_collectors → 不派新；未配置 no-op；无 room_id 跳过
- [ ] RED `test_danmaku_ingest.py`：jsonl → 行入库；重复 ingest 零新增（幂等）；半行（写入中截断）跳过不炸；文件名非 `\d+.jsonl` 忽略；item 不存在跳过；计数行字段（files/messages_inserted/messages_skipped）
- [ ] GREEN：dispatch.py + ingest.py + 三个 celery 任务壳 + beat 2 条目 + danmaku 队列路由 + 注册表断言；commit `feat: 弹幕派发器与幂等 ingest，beat 接线`

## Task 5: 文档 + 冒烟

- [ ] `.env.example` 新键；README「直播弹幕采集」小节（compose 起容器 → .env 三键 → 开播验收 → psql 查询验证，含 curl 探活与预期输出）；执行计划注记⑥
- [ ] commit `docs: Plan #5 弹幕采集使用文档与执行计划注记⑥`

## 风险与挂账

| 风险 | 处置 |
|---|---|
| douyinLive 签名被风控（DEVICE_BLOCKED 类） | 升级镜像 tag 为第一响应；当天切备选（用户裁决） |
| 首分片前弹幕缺失（F1） | 挂账 V2 always-on 采集器 |
| jsonl 与 metadata_json 无竞态但采集器无心跳入库 | 观测靠文件 mtime + ingest 计数行 + 任务返回值 |
| 单文件超大（超长直播） | 追加型写入无内存问题；ingest 全量重读（10MB 级）v1 可接受，增量 offset 挂账 |
| 弹幕量级冲击 LLM 分析 | F9 明示不做——本计划止步入库，采样/聚合策略由 EPIC-04 设计 |
