<!-- /autoplan restore point: "/Users/mshengran/.gstack/projects/FinanceOpinionRadar/main-autoplan-restore-20260917-134257.md" -->
## Implementation plan
# 抖音核心能力实施计划（Plan #4：VOD 账号跟踪 + 直播值守）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **本仓库既定约定（沿 Plan #1/#2/#3）**：用户要求不派 subagent，主会话逐任务串行执行；单分支 main 直推；提交规范 `feat|fix|refactor|docs|chore`。

**Goal:** 抖音博主"新视频自动跟踪"（账号级定时发现 → 自动转写）与"直播值守"（开播自动录制 → 分片转写）两条链路可用；补齐账号管理 API 作为两条链路的运营入口。

**Architecture:** 抖音签名战争（Argus/a_bogus）隔离在核心域之外——VOD 走外部解析服务（HTTP adapter，复用既有 `MediaSourceAdapter` Protocol 与 prepare 管线），直播走独立 Recorder Service（外部进程，产出分片文件，radar 消费）。radar 侧只做：per-platform adapter 分流、账号管理 API + live 字段迁移、直播分片 ingest 编排（偏移拼接 + 幂等）、值守配置同步。

**Tech Stack:** httpx（同步客户端，与 sync 栈一致）、既有 celery/beat/MinIO/faster-whisper 全复用、外部服务：Evil0ctal/Douyin_TikTok_Download_API（VOD 解析，docker compose 部署）+ ihmily/StreamCap（直播录制，Docker 部署）；f2（Johnserf-Seed）作为回退候选。

---

## 0. 背景与已定决策

### 用户优先级（2026-09-17 原话）

> "但是我最主要的还是要实现抖音的直播监控和抖音的这个博主的最新视频跟踪最新视频，这个是最重要的。这两个功能"

**执行计划顺序调整**：RAD-LIVE（§14 V1.5）按 PRD 本应"达到 PRD V1 门槛后才做"；本计划按用户明确优先级将其提前，与"抖音 VOD 账号发现"合并为 Plan #4。EPIC-04（观点抽取）顺延。此调整记入执行计划落地注记。

### 能力边界实测与调研（2026-09-17）

- **yt-dlp 抖音 = 长期不可用**（Plan #3 /qa 实测：完整游客 cookie ttwid+s_v_web_id、UA 对齐、nightly 2026.09.16 全部无效，Argus 设备指纹盾，上游留 TODO 未实现）。社区现状一致（yt-dlp issues #8090/#16803）。
- **VOD 候选**：
  | 候选 | 状态 | 集成形态 |
  |---|---|---|
  | Evil0ctal/Douyin_TikTok_Download_API | ✅ 活跃维护，docker compose 一键部署，REST+MCP，身份池自维护 | 外部 HTTP 服务（首选） |
  | jiji262/douyin-downloader | ✅ a_bogus+Playwright 双模 | CLI 子进程（备选） |
  | Johnserf-Seed/f2 | ✅ 可用，异步库，VOD+直播都覆盖 | 库/子进程（回退） |
  | JoeanAmier/TikTokDownloader | ❌ 合规原因签名算法停止维护，部分功能失效 | 弃 |
- **直播候选**：ihmily/StreamCap（ffmpeg+StreamGet，Docker 镜像，循环监控/定时任务/ts 分片）为首选；ihmily/DouyinLiveRecorder 为备选（老牌 60+ 平台，配置文件驱动）。
- **共同风险**：抖音风控持续升级，任何外部项目都可能间歇失效（Evil0ctal Discussion #548 有 400 案例）。**架构原则：核心域不 vendor 任何签名/风控对抗代码**，失效时升级外部服务镜像即可。

### 架构裁决（Task 1 spike 后冻结细节）

- **VOD**：`DouyinAdapter` 实现既有 `MediaSourceAdapter` Protocol，`discover/resolve` 走 Evil0ctal API HTTP 调用，`download_media` 用 httpx 直下 API 返回的无水印 play_addr；**抖音无 CC 字幕，`fetch_subtitle` 恒返 None**（prepare 管线自然走 ASR 路，零改动）。
- **直播**：一场直播 = 一条 `source_item`（`item_type='live'`，`external_item_id = live:{room_owner_external_id}:{yyyymmdd}` 承载 RAD-LIVE-05 去重，靠既有唯一约束）；每个分片独立 normalize→上传→ASR，transcript 行按"前序分片实际时长之和"偏移追加（RAD-LIVE-03）。录制分片静默期判定下播（V1 不做实时状态轮询的硬依赖）。
- **账号**：`source_account` 加 `live_monitor_enabled / expected_schedule / monitor_interval_sec`（RAD-LIVE-04，Alembic 迁移）+ `/source-accounts` CRUD API（`discovery_mode: manual|auto_poll` 的切换入口——此前账号只能经 create_item_from_url 隐式产生，无管理面）。
- **URL 安全**：新外部信任边界——API 返回的 CDN 直链必须过 `ensure_allowed_url`（新增 `douyin_cdn_allowlist`：douyinvod.com、aweme.snssdk.com 等），防解析服务被污染后 SSRF 进内网。API base_url 属可信配置不经此闸。

### 复用清单（不重写）

`prepare_source_item` 状态机/行锁/补扫、`SourceItemRepository.upsert_by_external` 去重、`dispatch_due_discoveries` 轮询 beat、`normalize_audio`、FasterWhisperProvider、MinIO storage、url_guard（douyin.com 已在默认白名单）。

---

## 文件结构（新增/修改）

```text
apps/api/app/services/media/adapters/douyin.py        # DouyinAdapter（API HTTP + httpx 下载）
apps/api/app/services/media/adapters/douyin_client.py # 低层 API 客户端（可独立 fake 的薄封装）
apps/api/app/services/media/factory.py                # per-platform 分流 + detect_platform(url)
apps/api/app/api/v1/source_accounts.py                # 账号 CRUD API
apps/api/app/api/v1/__init__.py                       # 挂新 router
apps/api/app/services/live_ingest.py                  # 直播分片扫描/会话聚合/偏移计算/幂等编排
apps/api/app/services/recorder_bridge.py              # StreamCap 配置同步（live_monitor_enabled → recorder）
apps/api/app/repositories/source_accounts.py          # +list_enabled_auto_poll/list_live_monitored
apps/api/app/repositories/media_assets.py             # +find_live_segment(item_id, index)
apps/api/app/worker/tasks.py                          # +discover/prepare 分流、ingest_live_segments、sync_live_monitors
apps/api/app/worker/celery_app.py                     # +2 个 beat 条目
apps/api/app/core/settings.py                         # douyin_api_base_url 等
apps/api/app/db/models/source.py                      # SourceAccount +3 列
apps/api/migrations/versions/xxxx_source_account_live_fields.py
tests/unit/test_douyin_adapter.py / test_douyin_client.py / test_factory_platform.py
tests/unit/test_live_ingest.py / test_recorder_bridge.py / test_source_accounts_api.py
tests/integration/test_live_ingest_flow.py
infra/docker/docker-compose.douyin.yml                # Evil0ctal API + StreamCap 两个外部服务声明
.env.example / README.md / 02_finance_opinion_radar_execution_plan.md（落地注记）
```

### Settings 新增（全部有默认值，dev 可降级）

```python
douyin_api_base_url: str = ""              # 空 = douyin adapter 不可用，构造即抛错（显式配置才启用）
douyin_api_timeout_sec: int = 30
douyin_cdn_allowlist: tuple[str, ...] = ("douyinvod.com", "aweme.snssdk.com", "zjcdn.com", "douyin.com")
live_segments_dir: str = ""                # 空 = live ingest 不启用（beat 每轮单行 no-op 日志可 grep；目录缺失 warning 不 crash）
live_scan_interval_sec: int = 300          # beat：分片扫描
live_close_grace_sec: int = 900            # 分片静默 ≥ 此值 → 会话视为下播收尾
live_min_segment_sec: int = 30             # 小于视为残片跳过
live_max_segments_per_session: int = 120   # 防失控（4h@2min 上限量级）
live_segment_max_attempts: int = 3         # 同分片连续失败 N 次后跳过记账（F4）
recorder_config_path: str = ""             # StreamCap config.yaml 共享卷路径，空 = 值守桥不启用
recorder_sync_interval_sec: int = 600
douyin_discover_max_pages: int = 3         # discover 翻页上限（与风险表对齐，E4）
```

---

## Task 1: 选型 spike（外部服务真栈验证，timebox 2h）

**目的**：在写第一行 adapter 代码前，用真实抖音 URL 验证两个外部服务的 2026-09 实际可用性，产出决策记录冻结 Task 2/5/6 的细节。

- [ ] **Step 1: 部署 Evil0ctal API**（`docker compose -f infra/docker/docker-compose.douyin.yml up -d crawler-services`），按其 README 配置 cookie 引导
- [ ] **Step 2: 用 3 个真实样本验证**：单视频 resolve（用户已提供 `modal_id=7686343567716269667`）、博主主页视频列表（用户提供其关注的博主）、无水印直链可下载性（httpx 下载 ≥1 个文件成功 + ffprobe 可读）
- [ ] **Step 3: 部署 StreamCap**（Docker Hub `ihmily/streamcap`），配置 1 个抖音直播间 room id，验证：开播检测→ts 分片落盘（无直播则用其文档/issue 确认抖音通道近期可用性即可，不硬等开播）
- [ ] **Step 4: 写决策记录**（追加到本文件 `## Task 1 决策记录`）：VOD 首选/回退判定、分片命名与目录结构实录（ingest 依赖它）、StreamCap 配置热更新方式（改 config.yaml 是否需重启/API）；**附加核对项**：①目录是否按日期切分、跨零点直播会不会拆目录（Task 5 合并规则依赖）；②API 响应中 `author.sec_uid` 字段完整性（VOD 手工贴链接与账号发现两路必须归到同一 sec_uid 账号，否则同一视频两条 item）；③主页列表翻页 maxCursor 语义实录；④StreamCap 是否回写自身 config.yaml（状态/统计段）——若回写，radar 的 sync 写入需节段隔离避免互相覆盖（E1）
- [ ] **Step 5: 决策门**——Evil0ctal 三个样本全败 → 切换 jiji262 CLI 路线重测 30min；再败 → f2 子进程路线；直播两项目全败 → 直播部分降级为"手工放分片文件进目录"的 ingest 先行（ingest 与录制解耦，不阻塞）
- [ ] **Step 6: commit** `docs: Plan #4 Task 1 选型 spike 决策记录`

## Task 2: DouyinAdapter（VOD，TDD）

**Files:** Create `adapters/douyin_client.py`、`adapters/douyin.py`；Modify `factory.py`、`apps/api/pyproject.toml`（+httpx 依赖）；Test `tests/unit/test_douyin_client.py`、`test_douyin_adapter.py`、`test_factory_platform.py`

- [ ] **Step 1: RED — client 单测**：`DouyinApiClient(base_url, timeout_sec)` 用 `httpx.MockTransport` 断言：`fetch_one_video(aweme_id)`、`fetch_user_posts(sec_uid, max_cursor, count)` 的路径/查询参数正确；非 2xx → `AdapterProcessError`，消息携带 upstream HTTP status 与截断 body（≤200 字符，进 `last_error` 供排障与 cookie 过期判别）；超时 → `AdapterTimeoutError`（两者均复用既有错误类型）
- [ ] **Step 2: GREEN**：实现 client（同步 httpx，方法体 ≤10 行，只做 HTTP+异常映射，不做字段映射）
- [ ] **Step 3: RED — adapter 单测**：fake client 返回 Evil0ctal 真实响应 JSON 样例（Task 1 实录存为 fixture `tests/fixtures/media/douyin_*.json`），断言：
  - `resolve`：`/video/{id}`、`/note/{id}`、`modal_id=` 提取 aweme_id；`v.douyin.com` 短链先 HEAD 跟随（跟随目标过 `ensure_allowed_url`）再 resolve；输出 `ResolvedMedia(item_type="vod", platform="douyin", channel_external_id=sec_uid, channel_url=user 页)`
  - `discover`：aweme_list → `DiscoveredItem(external_item_id=str(aweme_id), published_at=createTime, duration_ms=duration)`，置顶/置顶视频去重靠既有 upsert
  - `download_media`：play_addr URL 必须过 `ensure_allowed_url(douyin_cdn_allowlist)`（**SSRF 闸：伪造响应返回内网 URL → 抛 UrlNotAllowedError**，此测试必须先红）；httpx 流式写 workdir，返回 `DownloadResult(local_path, size_bytes)`
  - `fetch_subtitle` 恒返 None
  - **chaos（F8）**：API 200 但 body 缺字段（`aweme_detail=None`/缺 `author`）→ 抛 `AdapterProcessError` 而非 KeyError
- [ ] **Step 4: GREEN**：实现 adapter（client 持有 `httpx.Client` 连接复用，非每次新建）；`settings.douyin_api_base_url` 为空时构造抛 `AdapterError`，消息按 问题+原因+修复 三段（缺哪个键 `DOUYIN_API_BASE_URL`、意味着什么、README 排障锚点），沿「错误码速查」表风格
- [ ] **Step 5: RED→GREEN — factory 分流**：`detect_platform(url)`（host 含 douyin → "douyin"，youtu/be → "youtube"，bilibili → "bilibili"，其余 yt-dlp 兜底；实现前先查 discovery 既有 host 判断是否同逻辑，抽单一函数复用，E7）；`get_media_adapter(platform=None)` 按 platform 返回 DouyinAdapter / GenericYtDlpAdapter（无参调用兼容既有调用方）；`prepare_source_item` 任务改为 item→`source_account.platform` 查询后按平台取 adapter（补 1 条分流单测，F8；douyin 未配置时失败写 last_error，与其他 prepare 失败同语义）
- [ ] **Step 6: 全量 `pytest` + `ruff check` + `mypy app`**（apps/api 目录约定不变）
- [ ] **Step 7: commit** `feat: 抖音 VOD adapter 走外部解析服务，factory per-platform 分流`

## Task 3: 账号管理 API + live 字段迁移（TDD）

**Files:** Modify `db/models/source.py`、`repositories/source_accounts.py`；Create `api/v1/source_accounts.py`、迁移；Test `tests/unit/test_source_accounts_api.py`、`tests/integration/test_source_account_live_fields.py`

- [ ] **Step 1: RED — 模型/迁移**：`SourceAccount` + `live_monitor_enabled: bool = False`、`expected_schedule: dict | None (JSONB)`、`monitor_interval_sec: int = 300`；`alembic revision` 生成迁移（列默认值非空安全）；integration：迁移后可读写三字段
- [ ] **Step 2: RED — repository**：仅新增 `list_live_monitored()`（enabled + live_monitor_enabled，Task 6 调用方）。**不做 `list_enabled_auto_poll`**（CEO 审计 F1：现有 `list_due` 不筛 discovery_mode——enabled 即参与轮询是既有语义，YouTube/B 站 manual 账号一直被轮询且经 /qa 验证；v1 保持现状零破坏，`discovery_mode` 是意图标注字段，`enabled` 才是轮询开关，README 如实写明）
- [ ] **Step 3: RED — API 单测**（FastAPI TestClient，覆盖既有 conftest 模式）：
  - `POST /source-accounts`：`{platform, url, display_name?, discovery_mode?, poll_interval_sec?, config_json?}` → 201 get-or-create creator+account（复用 discovery.py 的 get-or-create 逻辑抽函数）；url 过 `ensure_allowed_url`；`discovery_mode` 仅接受 `manual|auto_poll`（422 校验）
  - `GET /source-accounts`：列表（platform/enabled 过滤）
  - `PATCH /source-accounts/{id}`：discovery_mode/poll_interval_sec/enabled/live 三字段局部更新（RAD-LIVE-04 入口）；404
- [ ] **Step 4: GREEN** 实现；`api/v1/__init__.py` 挂 router
- [ ] **Step 5: 既有 `dispatch_due_discoveries` 回归**：auto_poll 账号被正确扫到（既有测试全绿 + 补 1 条 auto_poll 用例）
- [ ] **Step 6: lint/mypy/全量测试；commit** `feat: 账号管理 API 与 source_account 直播值守字段`

## Task 4: 抖音 VOD 自动发现端到端（真栈，Task 2 之后）

- [ ] **Step 1: 手工验收脚本化**：用真实博主主页 sec_uid `POST /source-accounts`（platform=douyin, discovery_mode=auto_poll, poll_interval_sec=1800）
- [ ] **Step 2: 手动触发 `discover_account`**：≥2 条新视频入 `source_item`（status=discovered）→ 补扫/直投 prepare → transcript 落库（610 段级验证沿 Plan #3 方法）
- [ ] **Step 3: 发现的问题按 /qa 流程处置**；如有 adapter 缺口回 Task 2 补
- [ ] **Step 4: README「抖音 VOD 跟踪」小节 + commit** `feat: 抖音博主新视频自动跟踪链路验收`

## Task 5: 直播分片 ingest 编排（TDD，不依赖录制器）

**Files:** Create `services/live_ingest.py`；Modify `worker/tasks.py`、`worker/celery_app.py`；Test `tests/unit/test_live_ingest.py`、`tests/integration/test_live_ingest_flow.py`

**设计（RAD-LIVE-03/05，含 CEO 审计 F2/F3/F4/F5/F7/F10 修正）**：
- 会话 = `source_item(item_type='live')`，`external_item_id = live:{account_external_id}:{目录日期}`，靠唯一约束幂等；`metadata_json["live"] = {segment_count, last_segment_at, closed, segment_errors}`
- **跨零点合并（F2）**：同房间存在未收尾（closed≠true）会话且新分片落在相邻日期目录 → 延续既有 item 而非新建（目录日期只做目录键，会话身份以"未收尾优先"归并；具体以 Task 1 决策记录①实录为准）
- 分片序号从文件名解析（Task 1 实录的命名规则，v1 约定 `{n:04d}.ts` 归一化：扫描时 rename-free，用正则取 index）；**文件名只用于解析 index，绝不作为存储 key/下游路径的拼接源（F7，注入面）**
- 偏移：`base_offset_ms = SUM(前序**已处理**分片 media_asset.duration_ms)`（同一 item，按 segment_index 有序；被 min 时长跳过的残片不计入——转写时间轴以已转写内容为准，F5）；每分片一条 `prepare_live_segment(item_id, segment_index, path)` 任务：asset + transcript 行**同事务**原子落库（transcript 落库**复用既有 transcript repository 写入函数**含重叠校验，`sequence_no` 按偏移换算——禁止 live 路径重写校验规则，E3/DRY）（重跑前先查 `find_live_segment` 幂等跳出）；asset metadata 记录文件 mtime，分片间壁钟空洞 > live_close_grace_sec 时 log warning（F10，观测录制中断造成的洞，不阻断）
- **单飞闸（E2）**：`ingest_live_segments` 是长任务（首扫可达 120 分片×分钟级 ASR），celery beat 不去重、worker 并发 >1 时同任务重叠执行 → 偏移计算竞态。任务入口加单飞闸（沿既有 FOR UPDATE 行锁风格或运行标记）：抢不到锁立即退出并留单行日志；补 1 条并发拒绝用例（G3）
- **会话状态推进链（F3）**：ingest 建会话后按既有合法链逐级推进 `discovered→resolved→media_ready→transcribing`（无真实 resolve/download 阶段，直接连续推进），分片处理期间停驻 transcribing
- **分片重试上限（F4）**：同一 index 连续失败 ≥ `live_segment_max_attempts`（默认 3）次后跳过并记入 segment_errors[last_error]，不再每轮 beat 重试（防 ASR 失败循环烧 CPU）
- 会话收尾：扫描时 `now - last_segment_at > live_close_grace_sec` → status transcribing→transcribed（状态机 `"transcribing": {"transcribed", "failed"}` 已允许）

- [ ] **Step 1: RED — 会话聚合**：给定目录树 fake（tmp_path 造 `{date}/0000.ts...`），`scan_live_dir` 返回按会话分组的 (session_key, 有序分片路径)；`live_min_segment_sec` 以下跳过（ffprobe 时长由 fake binary 提供，沿 test_audio_normalize 的 fake 模式）
- [ ] **Step 2: RED — 幂等与偏移**：integration（真 PG）：新会话→建 item；seg0 处理后 transcript 起点 0；seg1 处理后起点 = seg0 实际时长；同分片重复投递 → 跳过且 transcript 行数不变；超 `live_max_segments_per_session` → 告警停扫；分片静默 > `live_close_grace_sec` → 会话收尾 transcribing→transcribed（G1 收尾用例）
- [ ] **Step 3: GREEN** 实现 `live_ingest.py` + `ingest_live_segments`（beat 扫描任务：扫目录→建会话→逐个未处理分片**同步串行**处理——顺序依赖偏移，V1 不并发同会话；每轮输出 `sessions_active/segments_pending/segments_failed` 计数行供运维 grep，F12）+ `dispatch_live_prepares` 保留作失败恢复（扫 transcribing 会话的缺号分片）
- [ ] **Step 4: 状态机复核**：分片处理失败 → 会话 item 保持 transcribing + `metadata_json["live"]["segment_errors"]` 记账（下轮重试缺号），整会话级错误才 failed
- [ ] **Step 5: lint/mypy/全量；commit** `feat: 直播分片 ingest 编排（会话聚合/偏移拼接/幂等）`

## Task 6: 值守桥——账号 → 录制器配置同步（TDD）

**Files:** Create `services/recorder_bridge.py`；Modify `worker/tasks.py`、`celery_app.py`；Test `tests/unit/test_recorder_bridge.py`

- [ ] **Step 1: RED**：`sync_live_monitors()`：`list_live_monitored()` → 生成 StreamCap 目标配置（room url=account.url, segment 时长取 monitor_interval_sec 夹紧到 [300,600]s；边界 300/600/越界三个夹紧用例，G2）→ 与 `recorder_config_path` 现文件 diff → 仅变化时原子写（tmp+rename）+ 触发 recorder 热加载方式按 Task 1 决策记录；`recorder_config_path` 空 → no-op 日志
- [ ] **Step 2: GREEN**；beat 条目 `sync-live-monitors`（recorder_sync_interval_sec）
- [ ] **Step 3: 真栈验证**：任一关注博主开播时段实测（或 StreamCap 指向任一公开测试直播间）：分片文件出现 → ingest 建会话 → 分片转写 → 下播静默后收尾 transcribed；commit `feat: 直播值守桥（账号订阅同步到录制器）`

## Task 7: 配置、文档与执行计划注记

- [ ] `.env.example`：新增全部 settings 键 + 注释（外部服务部署指引链接）
- [ ] `infra/docker/docker-compose.douyin.yml`：Evil0ctal API + StreamCap 服务声明（含共享卷 `live_segments`、cookie 配置挂载；本机 mirror 网络注意事项沿 radar-docker-env-quirks 记忆）。两个外部镜像 **pin 明确 tag**（非 latest，可复现）；README 升级段写明「升级外部镜像为第一响应」的操作序列（改 tag → pull → 冒烟 curl → 重启）
- [ ] `README.md`：抖音两条链路使用说明（账号注册→auto_poll；live 三字段→值守）。必须含「抖音 Quickstart」小节：从部署外部服务到第一条 transcript 的**有序命令序列 + 每步预期输出**（诚实 TTHW 预期 ≤10min，cookie 引导为不可压缩步骤）；`POST/PATCH /source-accounts` 完整 curl 示例（copy-paste 可用，样式沿「手工解析一个视频」节）；字段速查表区分 `poll_interval_sec`（发现轮询间隔）与 `monitor_interval_sec`（值守录制分片间隔）两个易混语义；排障表补 4 条：douyin API 不可达、Evil0ctal 400（cookie 过期，Discussion #548 同类）、StreamCap 无分片落盘、`douyin 未配置` 错误；诊断一条龙（`curl $DOUYIN_API_BASE_URL/docs` 探活、`ls live_segments/<账号>/` 目录观测）
- [ ] `02_finance_opinion_radar_execution_plan.md`：落地注记⑤（RAD-LIVE 提前裁决 + 实际实现映射）
- [ ] commit `docs: Plan #4 配置样例、使用文档与执行计划注记⑤`

## Task 8: /qa 真栈验收（链路第 6 步）

- [ ] 用户提供：≥1 个活跃抖音博主主页 URL + 1 个近期开播的直播间
- [ ] 验收清单：① auto_poll 账号 30min 级自动发现新视频并转写；② 直播开播→分片→transcript 追加→收尾全链；③ 既有 YouTube/B 站链路回归不受 factory 分流影响；④ /source-accounts API 全操作
- [ ] 发现按 /qa 流程处置；QA 报告落 `.gstack/qa-reports/`；后续沿链路：code review → /ship → /cso

---

## 风险与挂账

| 风险 | 处置 |
|---|---|
| 抖音风控再升级，外部服务间歇 400/失效 | 升级外部镜像为第一响应；jiji262/f2 双回退已入决策门；核心域零签名代码，切换成本低 |
| StreamCap 分片命名/目录与假设不符 | Task 1 决策记录实录后 Task 5 才动手；ingest 正则收敛为单一 `_SEGMENT_RE` 常量 |
| 无直播可测（验收时段博主没开播） | ingest 与录制解耦（Task 5 不依赖 Task 6）；录制链路用公开测试直播间或降级为文件投喂验收 |
| prepare_max_media_duration_sec=14400 对超长直播分片无效 | 分片粒度 ≤10min 天然规避；会话总时长不设限（分片逐个处理） |
| Evil0ctal API 对主页列表的翻页游标行为 | Task 1 Step 2 实录 maxCursor 语义，Task 2 discover 按 v1 只取第一页 ×N 翻页上限（`douyin_discover_max_pages`，默认 3） |

**明示不做（V1 边界）**：直播实时字幕流式抽取（EPIC-04 后）、弹幕采集、多录制器实例编排、douyin 评论区、TikTok 海外版。

<!-- autoplan-accepted:ceo -->
- F1 采纳：`list_due` 不筛 discovery_mode 为既有语义（source_accounts.py:55-74，enabled 即轮询，/qa 已验证），v1 零破坏；删除计划中无调用方的 `list_enabled_auto_poll`；`discovery_mode` 定位为意图标注、`enabled` 为轮询开关，README 如实写明。验证：既有 dispatch 测试全绿 + README 语义段。
- F2 采纳：跨零点直播合并规则——同房间未收尾会话延续既有 item（Task 1 决策记录①实录目录行为后冻结；Task 5 聚合规则已写入）。验证：integration 跨日期目录归并用例。
- F3 采纳：live 会话状态推进链显式化 `discovered→resolved→media_ready→transcribing`（合法迁移链连续推进），已写入 Task 5 设计。验证：ingest 单测断言停驻 transcribing。
- F4 采纳：分片重试上限 `live_segment_max_attempts=3`，超限跳过记账 segment_errors[last_error]，防 ASR 失败循环。验证：unit 失败计数用例。
- F5 采纳：偏移语义精确为 SUM(前序**已处理**分片 duration_ms)，残片跳过不计入。验证：integration 缺号/残片用例。
- F6 采纳：httpx 为新依赖，Task 2 Files 补 pyproject.toml；client 持有 `httpx.Client` 连接复用。验证：uv lock + 全量测试。
- F7 采纳：分片文件名仅作 index 解析源，禁止作为存储 key/下游路径拼接源。验证：unit 文件名注入用例（`../../x.ts` 类文件名不产生越界 key）。
- F8 采纳：补 chaos 用例（API 200 但 body 缺字段→AdapterProcessError）+ prepare 平台分流单测（douyin 未配置→failed+last_error）。验证：两条新单测。
- F9 采纳：live_segments_dir 以空目录起步 + README 注明首扫历史分片会整段补转写（受 live_max_segments_per_session 有界）。验证：README 段落。
- F10 采纳：分片 asset 记录 mtime，壁钟空洞 > grace 时 warning log（观测录制中断，不阻断）。验证：unit gap 警告用例。
- F11 采纳：Task 1 决策记录②核对 API 响应 sec_uid 字段完整性（两路归同一账号）。验证：决策记录条目 + fixture 断言。
- F12 采纳：ingest beat 每轮输出 sessions_active/segments_pending/segments_failed 计数行。验证：unit log 断言或人工验收。
- F13 挂账（不在本计划）：douyin 账号注册时 API 探活校验（v1 只做格式+白名单校验，探活使 API 变慢且有副作用）；live 会话标题填充（房间标题）；LiveAdapter.check_live 实装（v1 用静默期收尾替代）；多路直播并发值守的 ASR 容量扩展（单路 5-10min 分片 vs medium CPU ~2-4min/分片勉强跟上，多路会积压——观测靠 F12 计数行）。
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:dx -->
- DX-1 采纳（R1）：README「抖音 Quickstart」——从部署外部服务到首条 transcript 的有序命令序列+每步预期输出+诚实 TTHW≤10min；POST/PATCH /source-accounts curl 示例 copy-paste 可用。验证：README 小节逐条 curl 跑通。
- DX-2 采纳文档面（R1 并入）：README 字段速查表区分 `poll_interval_sec`（发现轮询）与 `monitor_interval_sec`（值守分片间隔）；列名本身保留（CEO 块已冻结），改名 vs 保留=TASTE DECISION 挂 Final Gate。验证：README 速查表。
- DX-3 采纳（R2）：`douyin_api_base_url` 为空的 AdapterError 消息按 问题+原因+修复 三段（缺哪个键、意味着什么、README 排障锚点）。验证：单测断言消息内容。
- DX-4 采纳（R3）：client 非 2xx → AdapterProcessError 携带 upstream HTTP status+截断 body（≤200 字符），进 last_error 供排障与 cookie 过期判别。验证：单测。
- DX-5 采纳（R5）：live ingest 空配置 beat 每轮单行 no-op 日志可 grep；目录缺失 warning 不 crash。验证：单测 log 断言。
- DX-6 采纳（R1 并入）：排障表补 4 条目——douyin API 不可达、Evil0ctal 400（cookie 过期，Discussion #548 同类）、StreamCap 无分片落盘、`douyin 未配置`。验证：README 排障表。
- DX-7 采纳（R4）：compose 两个外部镜像 pin 明确 tag（非 latest）；README 升级段写明升级操作序列（改 tag→pull→冒烟 curl→重启）。验证：grep 无 latest。
- DX-8 采纳（R1 并入）：README 诊断一条龙（`curl $DOUYIN_API_BASE_URL/docs` 探活、`ls live_segments/<账号>/` 观测）。验证：README 段落。
- 挂账（NOT in scope）：douyin API 依赖并入 /health 端点；抖音专用错误码进「错误码速查」表（积累 3+ 稳定码后）；上游项目文档汉化。
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- E1 采纳（R-E1）：Task 1 附加核对项④——实录 StreamCap 是否回写自身 config.yaml；若回写，sync 写入按节段隔离，防 radar 与录制器互相覆盖。验证：决策记录条目 + recorder_bridge 单测按节段读写。
- E2 采纳（R-E2）：ingest_live_segments 单飞闸（行锁/运行标记，抢不到即退+日志行）——celery beat 不去重，worker 并发下重叠执行会造成偏移竞态；补 G3 并发拒绝用例。验证：unit 并发拒绝用例。
- E3 采纳（R-E3）：live 分片 transcript 落库复用既有 repository 写入函数（含重叠校验），sequence_no 按偏移换算，禁止重写校验规则。验证：unit 断言校验路径被复用（重叠样本被拒）。
- E4 采纳（R-E4）：Settings 补 `douyin_discover_max_pages: int = 3`，与风险表对齐。验证：settings 实例化断言。
- E7 采纳（R-E7）：detect_platform 实现前核对 discovery 既有 host 判断，同逻辑抽单一函数复用。验证：factory/discovery 单测共用样例。
- G1 采纳（R-G1）：补会话收尾用例（静默 > grace → transcribing→transcribed）。验证：unit/integration 收尾用例。
- G2 采纳（R-G2）：补 recorder_bridge 夹紧边界 300/600/越界三用例。验证：unit。
- 失败模式终态：E2 竞态修复前为唯一 critical gap（无测试+无处理+静默产错数据），采纳后关闭；终态 0 critical gaps。
- 挂账（NOT in scope，沿 F13/既有注记）：douyin 账号注册 API 探活、live 会话标题填充、check_live 实装、多路 ASR 容量扩展、douyin API 依赖并入 /health、抖音专用错误码表。
<!-- /autoplan-accepted:eng -->
## Review record

<!-- autoplan-accepted:ceo -->
- F1 采纳：`list_due` 不筛 discovery_mode 为既有语义（source_accounts.py:55-74，enabled 即轮询，/qa 已验证），v1 零破坏；删除计划中无调用方的 `list_enabled_auto_poll`；`discovery_mode` 定位为意图标注、`enabled` 为轮询开关，README 如实写明。验证：既有 dispatch 测试全绿 + README 语义段。
- F2 采纳：跨零点直播合并规则——同房间未收尾会话延续既有 item（Task 1 决策记录①实录目录行为后冻结；Task 5 聚合规则已写入）。验证：integration 跨日期目录归并用例。
- F3 采纳：live 会话状态推进链显式化 `discovered→resolved→media_ready→transcribing`（合法迁移链连续推进），已写入 Task 5 设计。验证：ingest 单测断言停驻 transcribing。
- F4 采纳：分片重试上限 `live_segment_max_attempts=3`，超限跳过记账 segment_errors[last_error]，防 ASR 失败循环。验证：unit 失败计数用例。
- F5 采纳：偏移语义精确为 SUM(前序**已处理**分片 duration_ms)，残片跳过不计入。验证：integration 缺号/残片用例。
- F6 采纳：httpx 为新依赖，Task 2 Files 补 pyproject.toml；client 持有 `httpx.Client` 连接复用。验证：uv lock + 全量测试。
- F7 采纳：分片文件名仅作 index 解析源，禁止作为存储 key/下游路径拼接源。验证：unit 文件名注入用例（`../../x.ts` 类文件名不产生越界 key）。
- F8 采纳：补 chaos 用例（API 200 但 body 缺字段→AdapterProcessError）+ prepare 平台分流单测（douyin 未配置→failed+last_error）。验证：两条新单测。
- F9 采纳：live_segments_dir 以空目录起步 + README 注明首扫历史分片会整段补转写（受 live_max_segments_per_session 有界）。验证：README 段落。
- F10 采纳：分片 asset 记录 mtime，壁钟空洞 > grace 时 warning log（观测录制中断，不阻断）。验证：unit gap 警告用例。
- F11 采纳：Task 1 决策记录②核对 API 响应 sec_uid 字段完整性（两路归同一账号）。验证：决策记录条目 + fixture 断言。
- F12 采纳：ingest beat 每轮输出 sessions_active/segments_pending/segments_failed 计数行。验证：unit log 断言或人工验收。
- F13 挂账（不在本计划）：douyin 账号注册时 API 探活校验（v1 只做格式+白名单校验，探活使 API 变慢且有副作用）；live 会话标题填充（房间标题）；LiveAdapter.check_live 实装（v1 用静默期收尾替代）；多路直播并发值守的 ASR 容量扩展（单路 5-10min 分片 vs medium CPU ~2-4min/分片勉强跟上，多路会积压——观测靠 F12 计数行）。
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:dx -->
- DX-1 采纳（R1）：README「抖音 Quickstart」——从部署外部服务到首条 transcript 的有序命令序列+每步预期输出+诚实 TTHW≤10min；POST/PATCH /source-accounts curl 示例 copy-paste 可用。验证：README 小节逐条 curl 跑通。
- DX-2 采纳文档面（R1 并入）：README 字段速查表区分 `poll_interval_sec`（发现轮询）与 `monitor_interval_sec`（值守分片间隔）；列名本身保留（CEO 块已冻结），改名 vs 保留=TASTE DECISION 挂 Final Gate。验证：README 速查表。
- DX-3 采纳（R2）：`douyin_api_base_url` 为空的 AdapterError 消息按 问题+原因+修复 三段（缺哪个键、意味着什么、README 排障锚点）。验证：单测断言消息内容。
- DX-4 采纳（R3）：client 非 2xx → AdapterProcessError 携带 upstream HTTP status+截断 body（≤200 字符），进 last_error 供排障与 cookie 过期判别。验证：单测。
- DX-5 采纳（R5）：live ingest 空配置 beat 每轮单行 no-op 日志可 grep；目录缺失 warning 不 crash。验证：单测 log 断言。
- DX-6 采纳（R1 并入）：排障表补 4 条目——douyin API 不可达、Evil0ctal 400（cookie 过期，Discussion #548 同类）、StreamCap 无分片落盘、`douyin 未配置`。验证：README 排障表。
- DX-7 采纳（R4）：compose 两个外部镜像 pin 明确 tag（非 latest）；README 升级段写明升级操作序列（改 tag→pull→冒烟 curl→重启）。验证：grep 无 latest。
- DX-8 采纳（R1 并入）：README 诊断一条龙（`curl $DOUYIN_API_BASE_URL/docs` 探活、`ls live_segments/<账号>/` 观测）。验证：README 段落。
- 挂账（NOT in scope）：douyin API 依赖并入 /health 端点；抖音专用错误码进「错误码速查」表（积累 3+ 稳定码后）；上游项目文档汉化。
<!-- /autoplan-accepted:dx -->

### CEO 评审（in-host 单通道，SELECTIVE EXPANSION）

**系统审计**：main @ 732b197 干净（工作树仅本计划文件未跟踪）；无 stash；apps/api 无散落 TODO/FIXME（挂账全部走执行计划注记，纪律良好）；既往 /qa 修复集中在 yt-dlp adapter 与 worker（本计划触及区域，Task 2/5 需沿用修复后的调用形态：`run()` 与 `run_json()` 分离、任务注册表断言模式）。

**0A 前提挑战**：
1. "yt-dlp 抖音不可用"——已由本仓库 /qa 实测背书（cookie/UA/nightly 全试）✓ 成立。
2. "Evil0ctal API 可用"——仅 web 调研背书，未经真栈验证；计划以 Task 1 决策门收敛，前提风险已被结构化管理 ✓ 可接受。
3. "一场直播=一条 item 按日期 dedup"——跨零点场景有洞（→F2 修正）。
4. "偏移=SUM(前序分片时长)"——未定义"前序"边界（→F5 修正）。
5. "补齐账号管理 API"——用户两条链路的必要运营入口 ✓ 成立。
6. 无做与不做的分叉：用户明确"这两个功能最重要"，不做=核心场景为零 ✓。

**0B 既有代码复用**：discover/upsert 去重/prepare 状态机/补扫/normalize/ASR/storage/url_guard 全部复用；`dispatch_due_discoveries` 复用（发现 F1）；get-or-create 抽函数复用 discovery.py；`transcribing→transcribed` 迁移已允许（pipeline_states.py:7 ✓）。无重复建设。

**0C 梦想态**：CURRENT（抖音=手工单链）→ THIS PLAN（抖音账号矩阵自动发现+直播值守，per-platform adapter 模式成型）→ 12-MONTH（多平台矩阵+实时观点流+EPIC-04~10）。本计划把 adapter 边界从"yt-dlp 单例"升级为"per-platform 工厂"，正是后续快手/B站账号发现的直接模板——朝理想态移动。

**0C-bis 实现替代**：
```
APPROACH A: 外部解析服务 + 文件解耦 ingest（本计划）
  Effort: M  Risk: Med（外部项目可用性，已设决策门+双回退）
  Pros: 签名战争隔离在核心域外；失效=升级镜像；ingest 与录制解耦可独立验收
  Cons: 多两个容器；信任第三方项目处理 cookie
APPROACH B: vendor f2 库进核心
  Effort: M  Risk: High
  Pros: 少一个服务进程；VOD+直播一库覆盖
  Cons: 签名代码进域，风控升级即需自修；异步库嵌 sync 栈需要桥接
APPROACH C: 自研 a_bogus/Argus 逆向
  Effort: XL  Risk: High
  Pros: 零外部信任
  Cons: 军备竞赛不可持续，6 个月必被拖垮；完全违反核心域零签名原则
RECOMMENDATION: A（P1 完整度：唯一同时覆盖回退链与解耦验收的方案）
```

**0F 模式**：SELECTIVE EXPANSION（autoplan 覆盖规则）。0D 复杂度检查：触 ~20 文件 >8，归因于两个完整功能+管理面；已核对无可再砍的移动部件（Task 4 是验收非代码，Task 6 是用户需求本体）。扩展扫描结果全部在爆炸半径内且 <1d → 已采纳（F2/F4/F5/F7/F10/F12）；范围外项挂账（F13）。

**0E 时间审讯**：HOUR 1 = Task 1 决策记录是所有后续任务的输入（已强制前置）；HOUR 2-3 = API 响应嵌套/游标语义（fixture 实录解决）；HOUR 4-5 = mirror 网络拉镜像、StreamCap 热加载（决策记录解决）、ingest 首扫涌量（F9）；HOUR 6+ = 多路直播积压观测（F12/F13）。

### 架构图（Section 1）

```
 FastAPI :8001                       celery worker / beat
 ┌─────────────────────┐             ┌──────────────────────────────────┐
 │ /source-items       │             │ discover_source_account ──┐      │
 │ /source-accounts NEW│             │ prepare_source_item       │      │
 └────────┬────────────┘             │ ingest_live_segments  NEW │      │
          │ detect_platform(url)     │ sync_live_monitors    NEW │      │
          ▼                          └───────┬──────────▲────────┘      │
 ┌─────────────────────┐                     │        │ transcript    │
 │ get_media_adapter   │                     ▼        │               │
 │  (platform 分流 NEW)│             ┌──────────────┐ │  ┌────────────┴──┐
 └──┬──────────────┬───┘             │  Postgres    │ │  │ MinIO storage │
    │youtube/bili  │douyin           └──────────────┘ │  └───────────────┘
 ┌──▼──────────┐ ┌─▼──────────────┐    beat: dispatch_due_discoveries      │
 │ yt-dlp 子进程│ │ DouyinAdapter  │        dispatch_pending_prepares       │
 └─────────────┘ │  (NEW, httpx)  │        ingest_live_segments NEW        │
                 └─┬──────────┬───┘        sync_live_monitors  NEW            │
                   │HTTP      │CDN 直链(白名单闸)                               │
        ┌──────────▼───┐ ┌────▼─────────┐                              ┌──────┴────────┐
        │ Evil0ctal API│ │ douyinvod CDN│                              │ faster-whisper│
        │ (容器, 签名战争隔离) │ └──────────────┘                              └───────────────┘
        └──────────▲───┘
                   │cookie(自部署容器内)
        ┌──────────┴─────────────────────┐
        │ StreamCap 容器(自主循环值守)      │──ts 分片──▶ live_segments 共享卷
        └────────────────────────────────┘                    │
                                              radar ingest 只读消费（解耦边界）
```

四路（download_media 为例）：happy=API→play_addr→白名单→流式下载→normalize→ASR；nil=aweme 已删→404→AdapterProcessError→failed+last_error；empty=aweme_list 空→discover 返 []（正常）；error=API 超时→AdapterTimeoutError→504/failed。SSRF 路=内网 play_addr→UrlNotAllowedError（计划已内置先红测试）。

状态机：live 会话复用 PRD §11 既有状态机（无新状态），进入链显式化（F3），收尾 transcribing→transcribed 已允许。回滚：git revert + alembic downgrade（仅加列，向后兼容）；外部容器 down 即摘除；douyin_api_base_url 空=全链优雅降级。SPOF：Evil0ctal API（挂→douyin failed 可重试）、StreamCap（挂→分片静默，F12 计数行暴露）。

### Error & Rescue Registry（Section 2）

| 方法/路径 | 可能出错 | 异常 | 救援 | 用户可见 |
|---|---|---|---|---|
| DouyinApiClient.fetch_* | 非2xx/超时/坏JSON | AdapterProcessError/AdapterTimeoutError | prepare→failed(last_error)手工重试；API→502/504 | failed 状态+last_error |
| DouyinAdapter.resolve | 短链重定向失败/响应缺字段 | AdapterProcessError（F8 chaos） | 同上 | 同上 |
| DouyinAdapter.download_media | CDN URL 越白名单 | UrlNotAllowedError | 安全闸不重试 | failed（安全语义） |
| download 流中断 | httpx.StreamError | 包装为 AdapterProcessError | failed 可重试 | 同上 |
| prepare_live_segment | normalize/ASR 失败 | MediaAudioError 等 | 计数 ≤3 次重试后跳过记账（F4） | segment_errors 元数据 |
| ingest 扫描 | 目录缺失/不可读 | 配置错误 | error log + beat no-op | 运维日志 |
| recorder_bridge 写配置 | 磁盘/权限/热加载失败 | OSError | log + 下轮重试 | 运维日志 |
| discover 翻页 | 游标语义异常 | AdapterProcessError | max_pages 有界 | discover 计数减少 |

无 catch-all Exception 新增；复用既有错误族，映射规则沿 _map_adapter_errors。

### Failure Modes Registry

| CODEPATH | FAILURE MODE | RESCUED? | TEST? | USER SEES? | LOGGED? |
|---|---|---|---|---|---|
| douyin resolve | API 宕机 | Y | Y(unit fake) | failed+last_error | Y |
| douyin resolve | body 缺字段 | Y(F8) | Y | failed | Y |
| douyin download | SSRF 内网 URL | Y | Y(先红) | failed | Y |
| live ingest | 分片 ASR 连续失败 | Y(F4) | Y | segment_errors | Y |
| live ingest | 跨零点目录 | Y(F2) | Y | 单会话延续 | Y |
| live ingest | 首扫历史涌量 | Y(F9,有界) | N/A(运维约定) | 延迟转写 | Y |
| recorder bridge | 配置写失败 | Y | Y(unit) | no-op 重试 | Y |
| factory 分流 | douyin 未配置 | Y | Y(F8) | failed+last_error | Y |

CRITICAL GAP：0（全部行有救援+测试或运维约定）。

### Security & Threat Model（Section 3）

新攻击面：①/source-accounts CRUD——V1 无鉴权为已文档化债务（/cso LOW，EPIC-07/10），本计划不加新面（沿用 localhost 暴露面）；②douyin API SSRF——base_url 可信配置 + play_addr/短链重定向全过白名单（先红测试）；③分片路径注入——文件名只解析 index（F7）；④供应链信任——douyin cookie 交给自部署 Evil0ctal 容器，信任边界=该项目代码，V1 文档标注（容器不承载 radar 任何凭据）；⑤新增依赖 httpx（成熟，无新子进程面——相比再挂一个 yt-dlp 型子进程反而收窄）。likelihood/impact 均中低，计划已缓解。

### 数据流/边界（Section 4 摘要）

discover 翻页有界（max_pages=3）；upsert 双路归一（F11 核对 sec_uid）；ingest 幂等键 (item, index)；偏移只计已处理（F5）；首扫涌量有界（F9）；壁钟空洞观测（F10）。异步排序：同会话分片串行处理（V1 单 beat 串行，无并发交错面；多 worker 并发同会话由"每分片先查 find_live_segment+同事务落库"保证幂等，乱序结果=同值重算）。

### Code Quality（Section 5）/ Performance（Section 7）

质量：模块划分沿既有 services/ 模式；DRY（url_guard/get-or-create/状态机全复用）；无新抽象过度。性能：ASR 是既有瓶颈，单路直播 5-10min 分片 vs ~2-4min/分片转写可跟上；多路并发会积压（挂账 F13，观测 F12）；list_due limit 100 既有；无 N+1 新增（prepare 分流 +1 次 get，可忽略）。

### Observability（Section 8）/ Deploy（Section 9）/ Trajectory（Section 10）

观测：F12 计数行 + 既有 structlog 事件族（discover_ok/prepare_failed/ingest_*），log-first 与 V1 无 metrics 栈一致。部署：加列迁移零停机；外部容器可选启用（独立 compose 文件）；base_url 空=优雅降级；回滚=revert+downgrade。轨迹：per-platform 工厂是多平台化模板；债=expected_schedule 存而不用（RAD-LIVE-04 要求，文档标注）+第三方项目依赖（双回退备援）；可逆性 4/5。

### Section 11：SKIPPED（无 UI scope，Phase 0 判定）

### "NOT in scope"（含 F13 挂账）

见 accepted 块 F13：账号探活校验、live 标题填充、check_live 实装、多路 ASR 容量。外加既有明示不做段（实时字幕流/弹幕/多录制器/评论区/TikTok）。

### "What already exists"

discover→upsert→send 链（discovery.py）、prepare 状态机+行锁+补扫（preparation.py）、dispatch_due_discoveries/list_due（tasks.py/source_accounts.py）、normalize_audio/ASR/storage、url_guard（douyin.com 已白名单）、get-or-create 账号逻辑（discovery.create_item_from_url，Task 3 抽函数复用）。

### Dream state delta

本计划后：抖音=全链自动（发现+转写+直播值守），平台 adapter 模式成型；距 12 个月理想态还差：观点抽取（EPIC-04）、审核/前端（EPIC-05~08）、多平台矩阵扩展、直播实时化。

### CEO DUAL VOICES — CONSENSUS TABLE

```
═══════════════════════════════════════════════════════════════
  Dimension                             Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Premises valid?                    in-host  N/A    N/A
  2. Right problem to solve?            in-host  N/A    N/A
  3. Scope calibration correct?         in-host  N/A    N/A
  4. Alternatives sufficiently explored? in-host  N/A    N/A
  5. Competitive/market risks covered?  in-host  N/A    N/A
  6. 6-month trajectory sound?          in-host  N/A    N/A
═══════════════════════════════════════════════════════════════
OUTSIDE 不可用（codex model_unusable，沿 Plan #2/#3）；本仓库无
subagent 约定 → 单通道 in-host，六格 N/A 永不 CONFIRMED。
Native findings：13 项（F1-F12 采纳入计划，F13 挂账）。
```

### Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | ceo | 模式=SELECTIVE EXPANSION | Mechanical | autoplan 覆盖 | 既有系统迭代默认 | — |
| 2 | ceo | 方案 A（外部服务+解耦 ingest） | Mechanical | P1 | 唯一同时覆盖回退链与解耦验收 | B(f2 进域, P3/P5)、C(自研逆向, 不可持续) |
| 3 | ceo | F1 enabled=轮询开关语义 | Taste | P3/P5 | 零破坏既有行为+语义诚实 | list_due 改 auto_poll-only（破坏 YouTube 既有轮询） |
| 4 | ceo | F2-F12 采纳（细节见 accepted 块） | Mechanical | P1/P2 | 均在爆炸半径内且 <1d | — |
| 5 | ceo | F13 四项挂账 | Mechanical | P3 | 范围外或容量类 | — |

### Implementation Tasks
Synthesized from this review's findings. Each task derives from a specific finding above.

- [ ] **T1 (P1, human: ~1h / CC: ~10min)** — Task 1 决策记录附加核对项（跨零点①/sec_uid②/翻页③）
  - Surfaced by: 0A 前提 3/4、Section 4（F2/F11）
  - Files: docs/superpowers/plans/2026-09-17-plan4-douyin-core.md（Task 1 段已改）
  - Verify: 决策记录含三条实录
- [ ] **T2 (P1, human: ~30min / CC: ~10min)** — Task 3 收窄：删 list_enabled_auto_poll，discovery_mode 语义文档化
  - Surfaced by: 0B 代码事实（source_accounts.py:55）F1
  - Files: repositories/source_accounts.py、README.md（已改计划 Task 3 段）
  - Verify: 既有 dispatch 测试全绿
- [ ] **T3 (P1, human: ~1h / CC: ~20min)** — Task 5 设计修正落码：跨零点合并/状态推进链/偏移语义/重试上限/mtime 空洞/文件名注入闸
  - Surfaced by: Section 1/2/4（F2/F3/F4/F5/F7/F10）
  - Files: services/live_ingest.py、core/settings.py、tests/unit/test_live_ingest.py
  - Verify: integration 跨日期归并 + 缺号偏移 + 失败计数用例
- [ ] **T4 (P2, human: ~30min / CC: ~10min)** — Task 2 补齐：httpx 依赖、client 连接复用、chaos 缺字段用例、prepare 平台分流测试
  - Surfaced by: Section 1/5/6（F6/F8）
  - Files: pyproject.toml、adapters/douyin_client.py、worker/tasks.py、tests/unit/test_prepare_platform_routing.py
  - Verify: pytest 全绿 + uv lock diff
- [ ] **T5 (P2, human: ~20min / CC: ~5min)** — ingest beat 计数日志行（sessions_active/pending/failed）
  - Surfaced by: Section 8（F12）
  - Files: services/live_ingest.py
  - Verify: 运维 grep 字段存在
- [ ] **T6 (P2, human: ~20min / CC: ~10min)** — README 补 discovery_mode/enabled 语义 + live_segments_dir 空目录约定
  - Surfaced by: F1/F9
  - Files: README.md
  - Verify: 文档评审

<!-- autoplan-baseline-edits:ceo {"sourceSha256":"7bbf6a222047950b3da85e26d2d1063955375562c472abdcc1560624a5c0124d","replacements":[{"oldText":"- [ ] **Step 4: 写决策记录**（追加到本文件 `## Task 1 决策记录`）：VOD 首选/回退判定、分片命名与目录结构实录（ingest 依赖它）、StreamCap 配置热更新方式（改 config.yaml 是否需重启/API）","newText":"- [ ] **Step 4: 写决策记录**（追加到本文件 `## Task 1 决策记录`）：VOD 首选/回退判定、分片命名与目录结构实录（ingest 依赖它）、StreamCap 配置热更新方式（改 config.yaml 是否需重启/API）；**附加核对项**：①目录是否按日期切分、跨零点直播会不会拆目录（Task 5 合并规则依赖）；②API 响应中 `author.sec_uid` 字段完整性（VOD 手工贴链接与账号发现两路必须归到同一 sec_uid 账号，否则同一视频两条 item）；③主页列表翻页 maxCursor 语义实录"},{"oldText":"**Files:** Create `adapters/douyin_client.py`、`adapters/douyin.py`；Modify `factory.py`；Test","newText":"**Files:** Create `adapters/douyin_client.py`、`adapters/douyin.py`；Modify `factory.py`、`apps/api/pyproject.toml`（+httpx 依赖）；Test"},{"oldText":"- [ ] **Step 2: RED — repository**：`list_enabled_auto_poll()`（enabled + discovery_mode='auto_poll'，按 poll_interval 到期与否交给现有 dispatch 逻辑）、`list_live_monitored()`（enabled + live_monitor_enabled）","newText":"- [ ] **Step 2: RED — repository**：仅新增 `list_live_monitored()`（enabled + live_monitor_enabled，Task 6 调用方）。**不做 `list_enabled_auto_poll`**（CEO 审计 F1：现有 `list_due` 不筛 discovery_mode——enabled 即参与轮询是既有语义，YouTube/B 站 manual 账号一直被轮询且经 /qa 验证；v1 保持现状零破坏，`discovery_mode` 是意图标注字段，`enabled` 才是轮询开关，README 如实写明）"},{"oldText":"**设计（RAD-LIVE-03/05）**：\n- 会话 = `source_item(item_type='live')`，`external_item_id = live:{account_external_id}:{目录日期}`，靠唯一约束幂等；`metadata_json[\"live\"] = {segment_count, last_segment_at, closed}`\n- 分片序号从文件名解析（Task 1 实录的命名规则，v1 约定 `{n:04d}.ts` 归一化：扫描时 rename-free，用正则取 index）\n- 偏移：`base_offset_ms = SUM(前序分片 media_asset.duration_ms)`（同一 item，按 segment_index 有序）；每分片一条 `prepare_live_segment(item_id, segment_index, path)` 任务：asset + transcript 行**同事务**原子落库（重跑前先查 `find_live_segment` 幂等跳出）\n- 会话收尾：扫描时 `now - last_segment_at > live_close_grace_sec` → status transcribing→transcribed（状态机加 `\"transcribing\": {\"transcribed\", \"failed\"}` 已允许）","newText":"**设计（RAD-LIVE-03/05，含 CEO 审计 F2/F3/F4/F5/F7/F10 修正）**：\n- 会话 = `source_item(item_type='live')`，`external_item_id = live:{account_external_id}:{目录日期}`，靠唯一约束幂等；`metadata_json[\"live\"] = {segment_count, last_segment_at, closed, segment_errors}`\n- **跨零点合并（F2）**：同房间存在未收尾（closed≠true）会话且新分片落在相邻日期目录 → 延续既有 item 而非新建（目录日期只做目录键，会话身份以\"未收尾优先\"归并；具体以 Task 1 决策记录①实录为准）\n- 分片序号从文件名解析（Task 1 实录的命名规则，v1 约定 `{n:04d}.ts` 归一化：扫描时 rename-free，用正则取 index）；**文件名只用于解析 index，绝不作为存储 key/下游路径的拼接源（F7，注入面）**\n- 偏移：`base_offset_ms = SUM(前序**已处理**分片 media_asset.duration_ms)`（同一 item，按 segment_index 有序；被 min 时长跳过的残片不计入——转写时间轴以已转写内容为准，F5）；每分片一条 `prepare_live_segment(item_id, segment_index, path)` 任务：asset + transcript 行**同事务**原子落库（重跑前先查 `find_live_segment` 幂等跳出）；asset metadata 记录文件 mtime，分片间壁钟空洞 > live_close_grace_sec 时 log warning（F10，观测录制中断造成的洞，不阻断）\n- **会话状态推进链（F3）**：ingest 建会话后按既有合法链逐级推进 `discovered→resolved→media_ready→transcribing`（无真实 resolve/download 阶段，直接连续推进），分片处理期间停驻 transcribing\n- **分片重试上限（F4）**：同一 index 连续失败 ≥ `live_segment_max_attempts`（默认 3）次后跳过并记入 segment_errors[last_error]，不再每轮 beat 重试（防 ASR 失败循环烧 CPU）\n- 会话收尾：扫描时 `now - last_segment_at > live_close_grace_sec` → status transcribing→transcribed（状态机 `\"transcribing\": {\"transcribed\", \"failed\"}` 已允许）"},{"oldText":"live_max_segments_per_session: int = 120   # 防失控（4h@2min 上限量级）\nrecorder_config_path: str = \"\"             # StreamCap config.yaml 共享卷路径，空 = 值守桥不启用","newText":"live_max_segments_per_session: int = 120   # 防失控（4h@2min 上限量级）\nlive_segment_max_attempts: int = 3         # 同分片连续失败 N 次后跳过记账（F4）\nrecorder_config_path: str = \"\"             # StreamCap config.yaml 共享卷路径，空 = 值守桥不启用"},{"oldText":"- [ ] **Step 3: GREEN** 实现 `live_ingest.py` + `ingest_live_segments`（beat 扫描任务：扫目录→建会话→逐个未处理分片**同步串行**处理——顺序依赖偏移，V1 不并发同会话）+ `dispatch_live_prepares` 保留作失败恢复（扫 transcribing 会话的缺号分片）","newText":"- [ ] **Step 3: GREEN** 实现 `live_ingest.py` + `ingest_live_segments`（beat 扫描任务：扫目录→建会话→逐个未处理分片**同步串行**处理——顺序依赖偏移，V1 不并发同会话；每轮输出 `sessions_active/segments_pending/segments_failed` 计数行供运维 grep，F12）+ `dispatch_live_prepares` 保留作失败恢复（扫 transcribing 会话的缺号分片）"},{"oldText":"  - `fetch_subtitle` 恒返 None\n- [ ] **Step 4: GREEN**：实现 adapter；`settings.douyin_api_base_url` 为空时构造抛 `AdapterError(\"douyin 未配置\")`\n- [ ] **Step 5: RED→GREEN — factory 分流**：`detect_platform(url)`（host 含 douyin → \"douyin\"，youtu/be → \"youtube\"，bilibili → \"bilibili\"，其余 yt-dlp 兜底）；`get_media_adapter(platform=None)` 按 platform 返回 DouyinAdapter / GenericYtDlpAdapter；`prepare` 路径 platform 来自 `item.source_account.platform`","newText":"  - `fetch_subtitle` 恒返 None\n  - **chaos（F8）**：API 200 但 body 缺字段（`aweme_detail=None`/缺 `author`）→ 抛 `AdapterProcessError` 而非 KeyError\n- [ ] **Step 4: GREEN**：实现 adapter（client 持有 `httpx.Client` 连接复用，非每次新建）；`settings.douyin_api_base_url` 为空时构造抛 `AdapterError(\"douyin 未配置\")`\n- [ ] **Step 5: RED→GREEN — factory 分流**：`detect_platform(url)`（host 含 douyin → \"douyin\"，youtu/be → \"youtube\"，bilibili → \"bilibili\"，其余 yt-dlp 兜底）；`get_media_adapter(platform=None)` 按 platform 返回 DouyinAdapter / GenericYtDlpAdapter（无参调用兼容既有调用方）；`prepare_source_item` 任务改为 item→`source_account.platform` 查询后按平台取 adapter（补 1 条分流单测，F8；douyin 未配置时失败写 last_error，与其他 prepare 失败同语义）"}]} -->
### autoplan:dx（Phase 2.5，in-host 单通道，DX POLISH）

**Prior learning applied:** codex-exec-stdin-hang（9/10，2026-09-16——外审双重不可用，</dev/null+timeout 仍 MODEL_UNUSABLE）；autoplan-snapshot-baseline-edits（9/10，2026-09-16——amend 机制照办）。

**Step 0 范围评估**：产品类型 API/Service（dxScope 58 匹配：API×46/REST/CLI/MCP/integration）；受众=运维此系统的开发者本人（P6 依 README 推断）。初始 DX 完整度 6/10；TTHW 现估 12-15min（外部 compose 拉取+cookie 引导+.env+账号注册+首条转写）。既有链路 README 已优秀（快速开始/带预期输出的 curl/15 行排障表/错误码速查）；本计划新增两外部依赖把上手地板抬高。

**Dual voices**：Codex SAYS：unavailable（MODEL_UNUSABLE，探针输出已存 tool-results）。Claude SUBAGENT：unavailable（本仓库无 subagent 约定）→ in-host native 单通道。
```
DX DUAL VOICES — CONSENSUS TABLE:
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Getting started < 5 min?          5→8     —      N/A (outside unavailable)
  2. API/CLI naming guessable?         7→8     —      N/A
  3. Error messages actionable?        6→9     —      N/A
  4. Docs findable & complete?         5→8     —      N/A
  5. Upgrade path safe?                6→8     —      N/A
  6. Dev environment friction-free?    8       —      N/A
═══════════════════════════════════════════════════════════════
Missing outside voice = N/A, never CONFIRMED（单通道，与 CEO 相同）。
```

**Persona card**：Who=自托管后端开发者/唯一运维（mshengran 本人）；Context=本地 mac docker compose + GitHub Actions CI，抖音/YouTube/B 站三平台采集；Tolerance=外部服务部署+cookie 引导接受 ~10min，超过则弃用文档找 issue；Expects=.env 默认可用（既有模式「默认即开箱可用」）、curl 示例带预期输出、last_error 可读、排障表按症状索引。

**Empathy narrative（第一人称，依 README 实文）**：我 `make bootstrap` 起依赖、`make dev` 起 API——这条路径 README 写得很好，curl 带预期输出。现在换抖音：计划把我引向一个外部 compose 文件（Evil0ctal API）+cookie 引导，然后 11 个新 settings 键（空=禁用是好逃逸舱）。我第一个问题是「先配哪个？顺序是什么？」——计划里答案散在 Task 1/2/4/6/7 五处。我 curl `POST /source-accounts` 时得猜 body 字段；报 `douyin 未配置` 时消息没告诉我设哪个键；Evil0ctal 返 400 时我不知道是 cookie 过期还是接口变了（Discussion #548 有同类）。这些都是 POLISH 该堵住的洞。

**Competitive benchmark**：
```
Tool              | TTHW      | Notable DX Choice            | Source
Pinchflat         | ~1-2min   | 单容器、开箱即用             | github.com/kieraneglin/pinchflat, noted.lol/pinchflat
Tube Archivist    | ~10-20min | 3 容器（ta+redis+ES）重配置  | docs.tubearchivist.com/installation/docker-compose
本计划（修复前）   | 12-15min  | 3 容器形态但缺有序 quickstart | 本计划
本计划（修复后）   | ≤10min    | 有序命令序列+每步预期输出     | 修正后 README Quickstart
```
三容器形态对标 Tube Archivist；修复目标是把「重」变成「可预期的重」。

**Magical moment**（P5 最低成本载体）：账号注册后第一个 beat 周期，`make worker-beat` 日志出现 `discover_ok`、psql 查出 transcript 段落——沿 README DX-2A 验收模式复制到抖音链路（README Quickstart 的终步骤即此时刻，零额外构建物）。

**Journey map（9 段）**：
| # | Stage | 动作 | 摩擦点 | 处置 |
|---|---|---|---|---|
| 1 | Discover | 读 README 判断能否采抖音 | Plan #3 表格写「走 Plan #4 专属 adapter」 | ok（本计划落地后更新） |
| 2 | Install | 部署 Evil0ctal+StreamCap | 无 pin tag、无有序步骤 | DX-7/DX-1 修复 |
| 3 | Hello World | 第一条抖音 URL→transcript | cookie 引导步骤散落、无预期输出 | DX-1 修复 |
| 4 | Integrate | 账号注册→auto_poll | POST body 字段靠猜、两 interval 易混 | DX-1/DX-2 修复 |
| 5 | Real Usage | 直播值守三字段开启 | recorder 热加载方式待 Task 1 实录 | ok（决策门） |
| 6 | Debug | last_error/beat 日志 | 未配置错误无指引、upstream 无上下文、静默 no-op | DX-3/4/5 修复 |
| 7 | Upgrade | 外部镜像升级 | 无 tag pin、无升级序列 | DX-7 修复 |
| 8 | Scale | 多路直播并发 | ASR 容量（F13 挂账） | 挂账 |
| 9 | Migrate | 迁移 3 列（有默认值） | — | ok |

**8 pass 评分与裁决**（POLISH：每 gap 修，Findings 编号 DX-N，采纳即入 replacements）：
- Pass 1 Getting Started **5→8**：DX-1 采纳（R1）——README 增「抖音 Quickstart」有序命令+预期输出+诚实 TTHW≤10min+curl 示例。缺它，persona 会在 Task 1/2/4/6/7 五处跳找顺序。
- Pass 2 API/CLI **7→8**：DX-2 采纳文档面（并入 R1 字段速查表）；`poll_interval_sec` vs `monitor_interval_sec` 命名本身=**TASTE DECISION** 挂 Final Gate（改名成本 vs 语义清晰，CEO 已冻结列名）。
- Pass 3 Error Messages **6→9**：DX-3 采纳（R2，problem+cause+fix 三段）；DX-4 采纳（R3，upstream status+截断 body 进 last_error）；DX-5 采纳（R5，live ingest 空配置单行 no-op+缺目录 warning）。P1 覆盖规则「错误必须 问题+原因+修复」全落实。
- Pass 4 Documentation **5→8**：DX-6 采纳（并入 R1：排障表 4 条目+copy-paste curl）；测试 fixture JSON 兼作 API 响应文档（Task 1 实录）已具。
- Pass 5 Upgrade **6→8**：DX-7 采纳（R4，镜像 pin tag+升级操作序列）；3 列迁移有默认值零破坏。
- Pass 6 Dev Environment **8，无问题继续**：单测全 fake（MockTransport+fake binary）CI 零外部依赖；integration 沿真 PG 既有模式。
- Pass 7 Community **N/A（单运营者内部仓库），无问题继续**。
- Pass 8 Measurement **6→8**：DX-8 采纳（并入 R1 诊断一条龙）；F12 计数行已具；health 端点集成=明示不做。

**DX Scorecard**：
```
+====================================================================+
|              DX PLAN REVIEW — SCORECARD (autoplan:dx)               |
+====================================================================+
| Dimension            | Score   | Prior(#3) | Trend |
|----------------------|---------|-----------|-------|
| Getting Started      | 5 → 8   | 7         | ↑     |
| API/CLI/SDK          | 7 → 8   | 7         | ↑     |
| Error Messages       | 6 → 9   | 7         | ↑     |
| Documentation        | 5 → 8   | 7         | ↑     |
| Upgrade Path         | 6 → 8   | 6         | ↑     |
| Dev Environment      | 8       | 8         | —     |
| Community            | N/A     | N/A       | —     |
| DX Measurement       | 6 → 8   | 6         | ↑     |
+--------------------------------------------------------------------+
| TTHW                 | 12-15min → ≤10min（目标）                   |
| Competitive Rank     | Needs Work → Competitive（对标 Tube Archivist）|
| Magical Moment       | designed（beat discover_ok+transcript 段落）  |
| Product Type         | API/Service（operator-facing）               |
| Mode                 | DX POLISH                                    |
| Overall DX           | 6 → 8    | 7         | ↑     |
+====================================================================+
| DX PRINCIPLE COVERAGE                                               |
| Zero Friction      | gap→covered（Quickstart 有序化）               |
| Learn by Doing     | covered（curl 带预期输出沿既有模式）            |
| Fight Uncertainty  | gap→covered（三段式错误+诊断一条龙）            |
| Opinionated+Hatches| covered（空=禁用默认+显式配置启用）             |
| Code in Context    | covered（fixture 即真实响应样例）               |
| Magical Moments    | covered（零额外构建物，复用 beat 日志+psql）    |
+====================================================================+
```

**DX Implementation Checklist**：
```
[ ] TTHW ≤10min 有诚实预期且写入 Quickstart
[ ] 抖音 Quickstart 有序命令序列+每步预期输出
[ ] 首个可感知产出 = beat discover_ok + transcript 段落（零额外构建物）
[ ] 每条新错误消息含 问题+原因+修复（R2/R3/R5）
[ ] API 字段命名可猜；两 interval 语义速查表
[ ] 每个 settings 键有默认值且注释说明禁用语义
[ ] README curl 示例 copy-paste 可用
[ ] 排障表补抖音 4 条目（API 不可达/400 cookie 过期/无分片/未配置）
[ ] 升级路径：镜像 pin tag+升级操作序列
[ ] CI 零外部抖音依赖（单测全 fake）
[ ] live ingest 空配置/缺目录可观测（no-op 行+warning）
```

**What already exists（复用）**：README 快速开始/排障表/错误码速查三件套结构、curl+预期输出样式、`make doctor`、last_error 读取约定、.env.example 分组注释模式、单测 fake binary 模式、F12 计数行。

**NOT in scope（明示不做）**：douyin API 依赖并入 /health 端点（诊断 curl 已够，改健康语义需另立决策）；douyin 专用错误码进「错误码速查」表（V1 用 AdapterError 族消息即可，积累 3 个以上稳定码再入表）；前端 UI 引导（无 UI scope）；Evil0ctal/StreamCap 上游文档汉化。

**Implementation Tasks（DX 面向）**：
- [ ] **T1 (P1, human: ~1h / CC: ~10min)** — README — 抖音 Quickstart+字段速查+排障 4 条目+诊断一条龙（R1）
  - Surfaced by: Pass 1/2/4/8（DX-1/2/6/8）
  - Files: README.md
  - Verify: 对照小节逐条 curl 跑通
- [ ] **T2 (P2, human: ~30min / CC: ~10min)** — DouyinAdapter/client — 错误消息三段化+upstream 上下文（R2/R3）
  - Surfaced by: Pass 3（DX-3/4）
  - Files: apps/api/app/services/media/adapters/douyin*.py + 单测断言消息内容
  - Verify: pytest 单测含消息断言
- [ ] **T3 (P2, human: ~20min / CC: ~5min)** — compose — 镜像 pin tag+升级序列文档（R4）
  - Surfaced by: Pass 5（DX-7）
  - Files: infra/docker/docker-compose.douyin.yml, README.md
  - Verify: grep 无 latest
- [ ] **T4 (P2, human: ~15min / CC: ~5min)** — live_ingest — 空配置 no-op 行+缺目录 warning（R5）
  - Surfaced by: Pass 3（DX-5）
  - Files: apps/api/app/services/live_ingest.py
  - Verify: 单测 log 断言

**Unresolved decisions**：0（DX-2 命名之争为 TASTE DECISION，挂 Final Gate 面陈，非未决挂起）。

**TTHW assessment**：现估 12-15min（cookie 引导为不可压缩地板）；目标 ≤10min；不可达 Champion（<2min）——外部服务+cookie 引导是结构成本，POLISH 只承诺「可预期的重」。

**TASTE DECISIONS（Final Gate 面陈）**：①`monitor_interval_sec` 列名保留（CEO 已冻结）vs 改名 `live_segment_interval_sec` 语义自明——保留，以 README 速查表弥合；②排障表抖音条目暂用 AdapterError 消息而非新增错误码——积累后入表。
<!-- autoplan-baseline-edits:dx {"sourceSha256":"5f5a00d96e2e7486ae71ed5974a743b1a1267236fad96b43987364a3ab7449e7","replacements":[{"oldText":"- [ ] `README.md`：抖音两条链路使用说明（账号注册→auto_poll；live 三字段→值守）、排障表补抖音 API/录制器条目","newText":"- [ ] `README.md`：抖音两条链路使用说明（账号注册→auto_poll；live 三字段→值守）。必须含「抖音 Quickstart」小节：从部署外部服务到第一条 transcript 的**有序命令序列 + 每步预期输出**（诚实 TTHW 预期 ≤10min，cookie 引导为不可压缩步骤）；`POST/PATCH /source-accounts` 完整 curl 示例（copy-paste 可用，样式沿「手工解析一个视频」节）；字段速查表区分 `poll_interval_sec`（发现轮询间隔）与 `monitor_interval_sec`（值守录制分片间隔）两个易混语义；排障表补 4 条：douyin API 不可达、Evil0ctal 400（cookie 过期，Discussion #548 同类）、StreamCap 无分片落盘、`douyin 未配置` 错误；诊断一条龙（`curl $DOUYIN_API_BASE_URL/docs` 探活、`ls live_segments/<账号>/` 目录观测）"},{"oldText":"`settings.douyin_api_base_url` 为空时构造抛 `AdapterError(\"douyin 未配置\")`","newText":"`settings.douyin_api_base_url` 为空时构造抛 `AdapterError`，消息按 问题+原因+修复 三段（缺哪个键 `DOUYIN_API_BASE_URL`、意味着什么、README 排障锚点），沿「错误码速查」表风格"},{"oldText":"非 2xx → `AdapterProcessError`（复用既有错误类型）；超时 → `AdapterTimeoutError`","newText":"非 2xx → `AdapterProcessError`，消息携带 upstream HTTP status 与截断 body（≤200 字符，进 `last_error` 供排障与 cookie 过期判别）；超时 → `AdapterTimeoutError`（两者均复用既有错误类型）"},{"oldText":"- [ ] `infra/docker/docker-compose.douyin.yml`：Evil0ctal API + StreamCap 服务声明（含共享卷 `live_segments`、cookie 配置挂载；本机 mirror 网络注意事项沿 radar-docker-env-quirks 记忆）","newText":"- [ ] `infra/docker/docker-compose.douyin.yml`：Evil0ctal API + StreamCap 服务声明（含共享卷 `live_segments`、cookie 配置挂载；本机 mirror 网络注意事项沿 radar-docker-env-quirks 记忆）。两个外部镜像 **pin 明确 tag**（非 latest，可复现）；README 升级段写明「升级外部镜像为第一响应」的操作序列（改 tag → pull → 冒烟 curl → 重启）"},{"oldText":"live_segments_dir: str = \"\"                # 空 = live ingest 不启用","newText":"live_segments_dir: str = \"\"                # 空 = live ingest 不启用（beat 每轮单行 no-op 日志可 grep；目录缺失 warning 不 crash）"}]} -->

<!-- autoplan-accepted:eng -->
- E1 采纳（R-E1）：Task 1 附加核对项④——实录 StreamCap 是否回写自身 config.yaml；若回写，sync 写入按节段隔离，防 radar 与录制器互相覆盖。验证：决策记录条目 + recorder_bridge 单测按节段读写。
- E2 采纳（R-E2）：ingest_live_segments 单飞闸（行锁/运行标记，抢不到即退+日志行）——celery beat 不去重，worker 并发下重叠执行会造成偏移竞态；补 G3 并发拒绝用例。验证：unit 并发拒绝用例。
- E3 采纳（R-E3）：live 分片 transcript 落库复用既有 repository 写入函数（含重叠校验），sequence_no 按偏移换算，禁止重写校验规则。验证：unit 断言校验路径被复用（重叠样本被拒）。
- E4 采纳（R-E4）：Settings 补 `douyin_discover_max_pages: int = 3`，与风险表对齐。验证：settings 实例化断言。
- E7 采纳（R-E7）：detect_platform 实现前核对 discovery 既有 host 判断，同逻辑抽单一函数复用。验证：factory/discovery 单测共用样例。
- G1 采纳（R-G1）：补会话收尾用例（静默 > grace → transcribing→transcribed）。验证：unit/integration 收尾用例。
- G2 采纳（R-G2）：补 recorder_bridge 夹紧边界 300/600/越界三用例。验证：unit。
- 失败模式终态：E2 竞态修复前为唯一 critical gap（无测试+无处理+静默产错数据），采纳后关闭；终态 0 critical gaps。
- 挂账（NOT in scope，沿 F13/既有注记）：douyin 账号注册 API 探活、live 会话标题填充、check_live 实装、多路 ASR 容量扩展、douyin API 依赖并入 /health、抖音专用错误码表。
<!-- /autoplan-accepted:eng -->
### autoplan:eng（Phase 3，in-host 单通道，never-reduce，always last——审查最终 amended plan）

**Prior learning applied:** celery-include-tasks（9/10，2026-09-16——beat 派发行为与任务注册表教训直接支撑 E2）；codex-exec-stdin-hang（9/10——外审双重不可用第 4 次复现）。

**Step 0 Scope Challenge**（override: never reduce）：
- 复杂度检查触发（14 文件、4 新模块）——逐组件映射独立子问题，无并行建设物：DouyinAdapter/client=yt-dlp 无解的签名墙唯一出路（/qa 实测背书）；live_ingest=新域无既有代码；recorder_bridge=独立关注点（配置同步 vs 媒体处理，单文件 <400 行纪律）；账号 API=用户点名的运营入口。**Scope 维持，不裁剪。**
- 最小变更核验：无参 `get_media_adapter()` 兼容既有调用方；`list_due` 语义零改动（F1）；prepare 管线零改动（fetch_subtitle=None 走 ASR 路）。
- Search check：新增模式全部 Layer 1 成熟件（httpx 同步客户端、celery beat 周期任务、目录周期扫描、tmp+rename 原子写）；外部服务选型已带来源调研（计划 §0）；无自造基础设施，无 innovation token 支出。
- TODOS cross-ref：仓库无 TODOS.md（用户既定约定），挂账统一记计划「风险与挂账」+ F13/DX 挂账 + 执行计划注记——本计划新增挂账已并入 accepted 块尾条。
- 完整性 ✓（TDD 全程+chaos+integration）；分布 ✓（compose+.env.example+README+镜像 pin，CI 既有 6 jobs 不需扩）。

**Step 0.5 Dual voices**：Codex SAYS：unavailable（MODEL_UNUSABLE，探针 exit 1，第 4 次）。Claude SUBAGENT：unavailable（仓库无 subagent 约定）→ in-host native。
```
ENG DUAL VOICES — CONSENSUS TABLE:
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Architecture sound?               yes*    —      N/A (outside unavailable)
  2. Test coverage sufficient?         yes**   —      N/A
  3. Performance risks addressed?      yes     —      N/A
  4. Security threats covered?         yes     —      N/A
  5. Error paths handled?              yes     —      N/A
  6. Deployment risk manageable?       yes     —      N/A
═══════════════════════════════════════════════════════════════
*以 E1/E2 修正为条件；**以 G1-G3 补齐为条件。Missing outside voice = N/A, never CONFIRMED。
```

**Section 1 架构评审**（ASCII 依赖图）：
```
                    ┌────────────── radar core（既有，不变）──────────────┐
 API :8000 ─ /source-items ─ prepare_source_item ─ MinIO+whisper ─ transcript
   │                ▲                    │ adapter 按 platform 分流
   │                │                    ▼
   └─ /source-accounts(新)        get_media_adapter ─┬─ GenericYtDlpAdapter（yt-dlp）
        │ CRUD+live三字段                            └─ DouyinAdapter(新)⇄Evil0ctal API(外部)
        │                                                  │ play_addr httpx 流式下载
 celery beat ─┬─ dispatch_due_discoveries(既有)             │  [SSRF闸 douyin_cdn_allowlist]
              ├─ ingest_live_segments(新)─live_ingest─live_segments_dir(共享卷)▲
              │        │ 单飞闸(E2)                          │        StreamCap(外部容器)
              │        ├─ 会话=source_item(live) 唯一约束幂等 ┘
              │        └─ prepare_live_segment → normalize→ASR→transcript(偏移拼接)
              └─ sync_live_monitors(新)─recorder_bridge─recorder_config_path(原子写,节段隔离E1)→StreamCap
```
- 耦合评估：radar↔外部服务只经 3 个窄接口（HTTP API/文件目录/config 文件），核心域零签名代码——切换成本=换镜像，符合"boring by default"。
- **E1 [P1] (9/10)**：sync_live_monitors 与 StreamCap 自身可能互相覆盖 config.yaml——实录与节段隔离（Task 1④）。
- **E2 [P1] (9/10)**：celery beat 不去重（celery-include-tasks 教训同族），长任务重叠→偏移竞态——单飞闸。
- **E3 [P2] (8/10)**：transcript 落库规则两处实现风险（DRY）——强制复用既有写入函数。
- **E4 [P3] (8/10)**：`douyin_discover_max_pages` 在风险表出现但 Settings 块缺席——补键。
- 安全复检：SSRF 两闸（短链跟随目标、play_addr）、config 写入 URL 已过注册白名单、文件名只解析 index（F7）——无新发现。每新 codepath 生产失败场景已入下方失败模式表。
- 分布架构：compose+pin tag+升级序列（DX-7）✓。

**Section 2 代码质量评审**：
- **E7 [P3] (7/10)**：`detect_platform` 与 discovery 既有 host 判断潜在重复——先查再抽函数复用。
- 复用清单强制 DRY（normalize/whisper/storage/upsert/行锁全复用）；错误处理沿 AdapterError 族+_map_adapter_errors；文件规模符合 <400 行纪律。无其他 finding。

**Section 3 测试评审**（全 codepath 追踪，26 项）：
```
CODE PATHS / USER FLOWS（[+]新，★质量，GAP=计划缺口）
[+] douyin_client（R3 后）                                            [+] VOD 用户流（Task 4/8 验收）
  ├─ [计划✓★★★] 路径/参数/非2xx含status+body/超时                      ├─ [计划✓★★★] 账号→auto_poll→发现→转写（真栈）
[+] DouyinAdapter                                                     [+] live 用户流（Task 6.3/8）
  ├─ [计划✓★★★] resolve: modal_id//video//note/短链跟随+白名单          ├─ [计划✓★★★] 开播→分片→追加→收尾（真栈）
  ├─ [计划✓★★★] discover+chaos 缺字段                                  [+] 回归流
  ├─ [计划✓★★★] download: SSRF 先红/流式/size                          ├─ [计划✓★★★] YouTube/B站既有套件全绿+F8分流单测
  └─ [计划✓✓] fetch_subtitle None                                    （REGRESSION RULE：factory 分流改既有行为→既有套件
[+] factory（E7 后）                                                    即回归闸+F8 补分流用例 ✓）
  ├─ [计划✓★★★] detect_platform 四分支
  └─ [计划✓★★] 分流+无参兼容（F8）
[+] /source-accounts API
  ├─ [计划✓★★★] 201 get-or-create/422 枚举/404/白名单400
  └─ [计划✓★★] list_live_monitored
[+] 迁移 3 列 [计划✓★★] integration 读写
[+] live_ingest
  ├─ [计划✓★★★] scan 分组/min跳过/幂等/偏移SUM已处理(F5)/跨零点(F2)
  ├─ [计划✓★★] max_segments 停扫 / F4 重试上限 / F10 mtime 空洞
  ├─ [GAP→G1] 收尾 grace→transcribed 用例（R-G1 补）
  ├─ [GAP→G3] 单飞闸并发拒绝用例（R-E2 补）
  └─ [计划✓★★] F3 状态链停驻 transcribing
[+] recorder_bridge
  ├─ [计划✓★★] diff 只变化时原子写/空路径 no-op
  └─ [GAP→G2] 夹紧边界 300/600/越界（R-G2 补）
LLM/eval：无 prompt 变更 → N/A
COVERAGE: 计划已覆盖 23/26（88%）| GAPS: 3（全部随 R-E1/E2/G1/G2 采纳关闭）
QUALITY: ★★★:12 ★★:9 | E2E: Task 4/6.3/8 真栈
```
- Test plan artifact：`~/.gstack/projects/FinanceOpinionRadar/mshengran-main-eng-review-test-plan-20260917-183900.md`（供 /qa 消费）。

**Section 4 性能评审**：偏移 SUM 查询规模有界（≤120 分片/会话，索引列），单飞串行语境无 N+1 热点；ASR CPU 容量=F13 挂账（F12 计数行观测积压）；httpx 连接复用（F6）。无新 finding。

**失败模式登记表**（每新 codepath 一行：失败模式 | 测试 | 错误处理 | 可见性）：
| codepath | 失败模式 | 测试 | 处理 | 可见 |
|---|---|---|---|---|
| douyin_client | API 400/超时/缺字段 | chaos+状态码 | AdapterError 族→last_error | ✓ |
| 短链/play_addr | 解析服务被污染返内网 URL | SSRF 先红 | UrlNotAllowedError | ✓ |
| live 会话聚合 | 目录缺失/命名漂移 | unit fake | warning 不 crash（R5） | ✓ |
| 偏移拼接 | worker 重叠执行 | **G3**（补） | **单飞闸 E2**（补） | 日志行 |
| 分片 ASR | 连续失败循环 | F4 计数 | 上限跳过+segment_errors | ✓ |
| 会话收尾 | 永不收尾 | **G1**（补） | grace 静默判定 | ✓ |
| recorder config | 双写互覆 | **E1 实录**（补） | 节段隔离 | ✓ |
| 值守同步 | 配置漂移 | **G2**（补） | diff+原子写 | ✓ |
修复后 critical gaps=0（修复前 E2 为唯一 critical：无测试+无处理+静默错数据）。

**What already exists（复用，不重建）**：prepare 状态机/行锁/补扫、upsert_by_external、dispatch beat、normalize_audio、FasterWhisperProvider、MinIO storage、url_guard、_map_adapter_errors、TestClient conftest 模式、fake binary 测试模式——计划复用清单已全部声明 ✓。
**NOT in scope**：见 accepted:eng 尾条（F13+DX 挂账+health 集成+错误码表）。
**Parallelization**：Sequential implementation（仓库既定主会话串行约定），no parallelization opportunity。

**Implementation Tasks（eng 面向）**：
- [ ] **T1 (P1, human: ~15min / CC: ~5min)** — Task 1 spike — 附加核对项④ StreamCap config 回写实录（E1）
- [ ] **T2 (P1, human: ~1h / CC: ~15min)** — live_ingest — 单飞闸+G3 并发拒绝用例（E2）
- [ ] **T3 (P2, human: ~30min / CC: ~10min)** — live_ingest — transcript 落库复用既有写入函数（E3）
- [ ] **T4 (P2, human: ~20min / CC: ~5min)** — 测试补齐 — G1 收尾用例+G2 夹紧边界+E4 Settings 键
- [ ] **T5 (P3, human: ~15min / CC: ~5min)** — factory/discovery — detect_platform 去重复用（E7）

**Completion summary**：
- Step 0: Scope Challenge — 复杂度触发但 never-reduce，逐组件映射后 scope 维持
- Architecture Review: 4 issues（E1/E2 P1、E3 P2、E4 P3）
- Code Quality Review: 1 issue（E7）
- Test Review: diagram produced, 3 gaps（G1-G3，全采纳补齐）
- Performance Review: 0 issues
- NOT in scope / What already exists: written
- TODOS.md updates: 0（仓库无 TODOS.md 约定，挂账入计划挂账段）
- Failure modes: 修复前 1 critical gap（E2），采纳后 0
- Outside voice: unavailable（codex MODEL_UNUSABLE 第 4 次）；native in-host
- Parallelization: sequential（repo 约定）
- Lake Score: 7/7 全部采纳完整方案
- Unresolved decisions: 0
<!-- autoplan-baseline-edits:eng {"sourceSha256":"39dc4902eefe8a92ec0da94dcb613349957787efcb7455efe59ac6615dace893","replacements":[{"oldText":"③主页列表翻页 maxCursor 语义实录","newText":"③主页列表翻页 maxCursor 语义实录；④StreamCap 是否回写自身 config.yaml（状态/统计段）——若回写，radar 的 sync 写入需节段隔离避免互相覆盖（E1）"},{"oldText":"- **会话状态推进链（F3）**：ingest 建会话后按既有合法链逐级推进","newText":"- **单飞闸（E2）**：`ingest_live_segments` 是长任务（首扫可达 120 分片×分钟级 ASR），celery beat 不去重、worker 并发 >1 时同任务重叠执行 → 偏移计算竞态。任务入口加单飞闸（沿既有 FOR UPDATE 行锁风格或运行标记）：抢不到锁立即退出并留单行日志；补 1 条并发拒绝用例（G3）\n- **会话状态推进链（F3）**：ingest 建会话后按既有合法链逐级推进"},{"oldText":"每分片一条 `prepare_live_segment(item_id, segment_index, path)` 任务：asset + transcript 行**同事务**原子落库","newText":"每分片一条 `prepare_live_segment(item_id, segment_index, path)` 任务：asset + transcript 行**同事务**原子落库（transcript 落库**复用既有 transcript repository 写入函数**含重叠校验，`sequence_no` 按偏移换算——禁止 live 路径重写校验规则，E3/DRY）"},{"oldText":"recorder_sync_interval_sec: int = 600","newText":"recorder_sync_interval_sec: int = 600\ndouyin_discover_max_pages: int = 3         # discover 翻页上限（与风险表对齐，E4）"},{"oldText":"超 `live_max_segments_per_session` → 告警停扫","newText":"超 `live_max_segments_per_session` → 告警停扫；分片静默 > `live_close_grace_sec` → 会话收尾 transcribing→transcribed（G1 收尾用例）"},{"oldText":"（room url=account.url, segment 时长取 monitor_interval_sec 夹紧到 [300,600]s）","newText":"（room url=account.url, segment 时长取 monitor_interval_sec 夹紧到 [300,600]s；边界 300/600/越界三个夹紧用例，G2）"},{"oldText":"`detect_platform(url)`（host 含 douyin → \"douyin\"，youtu/be → \"youtube\"，bilibili → \"bilibili\"，其余 yt-dlp 兜底）","newText":"`detect_platform(url)`（host 含 douyin → \"douyin\"，youtu/be → \"youtube\"，bilibili → \"bilibili\"，其余 yt-dlp 兜底；实现前先查 discovery 既有 host 判断是否同逻辑，抽单一函数复用，E7）"}]} -->
