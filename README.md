# 财经观点雷达 / Finance Opinion Radar

聚合多源财经内容，抽取核心观点并呈现多空情绪雷达。

需求与设计见 `01_finance_opinion_radar_PRD.md`；任务拆解见 `02_finance_opinion_radar_execution_plan.md`。

## 快速开始

前置：Python 3.12、Node ≥ 22、Docker Desktop。

```bash
make setup        # 创建 .venv 并安装后端/前端依赖
make bootstrap    # 启动 postgres/redis/minio + 迁移 + 种子数据（幂等）
make dev          # 启动 API（默认 :8000）
```

另开终端验证：

```bash
curl -fsS localhost:8000/api/v1/health
# {"status":"ok"}
```

前端：`make web`（Vite 开发服务器，http://localhost:5173）。API 交互文档：http://localhost:8000/docs。

### 手工解析一个视频（EPIC-02）

```bash
curl -fsS -X POST localhost:8000/api/v1/source-items/resolve-url \
  -H 'content-type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=<视频id>"}'
# {"platform":"youtube","external_id":"…","title":"…","duration_ms":…,
#  "thumbnail_url":"…","item_type":"vod","published_at":"…","channel_name":"…",
#  "subtitle_languages":["zh-Hans","en"]}
```

预览无误后 `POST /api/v1/source-items`（同 body）落库为 `source_item(status=discovered)`，重复提交幂等。定时发现：`make worker-beat` 启动 worker+beat，tail 日志观察 `discover_ok` / `discover_failed`。

### 自动转录：一个 URL 变 transcript（EPIC-03）

`POST /api/v1/source-items` 落库后 worker 自动接手（需 `make worker-beat` 在跑）：
resolve → 字幕优先（zh-Hans/zh/en）→ 无可用字幕则下载音频 → ffmpeg 标准化（mono/16kHz wav）→ faster-whisper 转录 → `transcript_segment` 落库，`source_item.status` 走 discovered→resolved→media_ready→transcribing→transcribed。失败不抛任务异常，读 `source_item.metadata_json.last_error`（见下方错误码速查）。

验收——直接在库里看见段落（DX-2A）：

```bash
docker compose exec postgres psql -U radar -d radar -c \
  "SELECT sequence_no, start_ms, end_ms, left(text,30) FROM transcript_segment WHERE source_item_id=<id> ORDER BY sequence_no LIMIT 5;"
```

`cp .env.example .env` 可选——dev 环境默认值即开箱可用；仅当本地端口被占用时才需要覆盖（见下方排障）。

## 抖音（Plan #4）

抖音与 YouTube/B 站（内置 yt-dlp 路径）分流：VOD（博主新视频）走外部 [Evil0ctal dtk 解析服务](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)，直播录制走外部 [StreamCap](https://github.com/ihmily/StreamCap)，两个外部容器由 `infra/docker/docker-compose.douyin.yml` 声明，与 radar 自身 compose 独立。未配置 `DOUYIN_API_BASE_URL` 时 douyin 链路整体不可用，其余平台不受影响。

### 抖音 Quickstart

从零到第一条抖音 transcript：机械步骤合计约 10 分钟（外加镜像首次拉取时间）。**导出 cookie 是唯一不可压缩的人工步骤**，需要登录过抖音的浏览器。

前置（一次性）：导出 Netscape 格式 cookies.txt 后**必须按域过滤出纯 `.douyin.com` 子集**（多平台混合 jar 会被 dtk 400 拒收），并记下导出浏览器的 User-Agent（第 4 步要原样回传）：

```bash
grep '\.douyin\.com' ~/Downloads/cookies.txt > ~/.config/radar/douyin.cookies.txt
chmod 600 ~/.config/radar/douyin.cookies.txt
```

第 1 步——起外部栈（compose 内置 migrate→api/worker 顺序闸与 StreamCap）：

```bash
export DTK_SECRET_KEY="$(openssl rand -base64 48)"   # dtk 凭据主密钥，固化到 shell profile；换钥则已存身份不可解
docker compose -f infra/docker/docker-compose.douyin.yml up -d
docker compose -f infra/docker/docker-compose.douyin.yml ps
# 预期：dtk-migrate Exited(0)；douyin-api/dtk-worker/dtk-postgres/dtk-redis/streamcap Up
curl -fsS localhost:8080/api/setup/status
# {"success":true,"data":{"initialized":false},...}
```

第 2 步——dtk 首次引导（仅首次部署；管理员账号 + 会话 cookie）：

```bash
TOKEN=$(docker logs douyin-api 2>&1 | grep -o 'setup?token=[A-Za-z0-9_-]*' | head -1 | cut -d= -f2)
curl -fsS -X POST localhost:8080/api/setup/init -H 'content-type: application/json' \
  -d "{\"token\":\"$TOKEN\",\"username\":\"radar\",\"password\":\"<自定管理员密码>\"}"
curl -fsS -c /tmp/dtk-jar -X POST localhost:8080/api/v1/auth/login \
  -H 'content-type: application/json' -d '{"username":"radar","password":"<同上>"}'
# 预期 {"success":true,...}；会话落 /tmp/dtk-jar，admin 接口用 -b /tmp/dtk-jar
```

第 3 步——造 radar 用的 API key（scopes 必填，radar 全链依赖这四项；明文只在创建响应出现一次）：

```bash
curl -fsS -b /tmp/dtk-jar -X POST localhost:8080/api/v1/admin/api-keys \
  -H 'content-type: application/json' \
  -d '{"name":"radar","scopes":["douyin:read","media:read","media:write","archive:read"]}' \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["data"]["secret"])'
# 输出 dtk_… 即 DOUYIN_API_KEY
```

第 4 步——导入抖音 cookie（身份池为空时任务报 `IDENTITY_POOL_EXHAUSTED`）：

```bash
curl -fsS -b /tmp/dtk-jar -X POST localhost:8080/api/v1/admin/identities/import \
  -H 'content-type: application/json' \
  -d "$(jq -n --rawfile c ~/.config/radar/douyin.cookies.txt \
        '{platform:"douyin",cookies:$c,user_agent:"<导出该 jar 的浏览器 UA>"}')"
# 预期 {"success":true,"data":{"stored":true,"identity_id":"…"}}
```

第 5 步——radar 侧接线（`.env` 取消注释并填写）后起 worker：

```bash
# DOUYIN_API_BASE_URL=http://localhost:8080
# DOUYIN_API_KEY=dtk_…                                # 第 3 步
# LIVE_SEGMENTS_DIR=data/douyin/live_segments        # 与 compose 共享卷同目录
# RECORDER_CONFIG_PATH=data/douyin/streamcap-config/recordings.json
make worker-beat
```

第 6 步——VOD 链验收（注册账号 → 自动发现 → 自动转写）。URL 必须是主页形态 `https://www.douyin.com/user/<sec_uid>`（搜索页解析不出 sec_uid，422 拒收）：

```bash
curl -fsS -X POST localhost:8000/api/v1/source-accounts \
  -H 'content-type: application/json' \
  -d '{"platform":"douyin","url":"https://www.douyin.com/user/<sec_uid>","display_name":"<名字>","discovery_mode":"auto_poll","poll_interval_sec":1800}'
# 201 返回账号（enabled 默认 true）；beat 日志出现 discover_ok 后 prepare 自动接手
```

transcript 验收沿「自动转录」节的 SQL（`WHERE source_item_id=<新视频 id>`）。

第 7 步——直播值守链（同一账号加三个字段）：

```bash
curl -fsS -X PATCH localhost:8000/api/v1/source-accounts/<账号id> \
  -H 'content-type: application/json' \
  -d '{"live_monitor_enabled":true,"monitor_interval_sec":600}'
# 值守桥 beat（默认 10min）写入 data/douyin/streamcap-config/recordings.json 并 docker restart streamcap
```

**关键：StreamCap 配置不热重载**——每次重启后需打开一次 http://localhost:5001（Web UI）激活监控循环，此后循环与浏览器解耦。开播后 `ls data/douyin/live_segments/douyin/<主播>/<日期>/` 看分片出现；transcript 追加沿上方 SQL（会话条目 `item_type='live'`），下播静默 15min 后会话收尾。

### 抖音 VOD 跟踪（Task 4 真栈验收实录，2026-09-17）

新闻联播替身账号（sec_uid 样本）端到端实测：注册 auto_poll 账号 → 手动触发一次 discover（3 页 60 条）→ prepare 自动接队 → **37/60 条转写完成（1398 段 transcript）**，段落为真实中文文本；余量为可重试的运营性失败（见下）。

**首扫涌量预期**：高频更新博主首轮 discover 一次拉满 `DOUYIN_DISCOVER_MAX_PAGES`×20 条，60 个解析/下载请求打向 dtk 与 douyinvod CDN，单身份池会被打爆（`IDENTITY_POOL_EXHAUSTED`）、CDN 会限速 403——**这不是链路故障**，稳定运行后每轮增量只有 0–2 条，无此问题。首轮残余 failed 项的恢复操作（prepare 白名单允许 failed 重跑，只是不自动补扫）：

```bash
# 低峰分批重驱（每批 4-5 条间隔 30s，避免再次触发限速）
docker exec financeopinionradar-postgres-1 psql -U radar -d radar -tAc \
  "SELECT id FROM source_item WHERE source_account_id=<账号id> AND status='failed'" \
| xargs .venv/bin/python -c "
import sys, time
from app.worker.celery_app import celery_app
ids = [int(x) for x in sys.stdin if x.strip()]
for i in range(0, len(ids), 4):
    [celery_app.send_task('prepare_source_item', args=[j]) for j in ids[i:i+4]]
    if i + 4 < len(ids): time.sleep(30)
"
```

失败分类速查：`IDENTITY_POOL_EXHAUSTED`/CDN 403 = 限速，冷却后重驱即可；`TRANSCRIPT_FAILED`（清洗后 0 段）= 视频无有效语音（纯音乐/空拍），属保护性拒绝，不重驱。

字段速查——两个 interval 别混：

| 字段 | 语义 | 默认 |
|---|---|---|
| `poll_interval_sec` | VOD 发现轮询间隔：auto_poll 账号多久 discover 一次新视频 | 3600 |
| `monitor_interval_sec` | 直播值守录制分片时长：映射 StreamCap `segment_time`，夹紧到 300–600s | 600 |

`PATCH /source-accounts/<id>` 全量字段：`discovery_mode` / `poll_interval_sec` / `enabled` / `live_monitor_enabled` / `monitor_interval_sec` / `expected_schedule`。

升级外部镜像（douyin 链路异常的第一响应）——改 tag → pull → 冒烟 → 重启：

```bash
# 1) 编辑 infra/docker/docker-compose.douyin.yml 的 image: 行（dtk 与 streamcap 两处）
docker compose -f infra/docker/docker-compose.douyin.yml pull douyin-api streamcap
curl -fsS -o /dev/null -w '%{http_code}\n' localhost:8080/docs   # 2) 冒烟，预期 200
docker compose -f infra/docker/docker-compose.douyin.yml up -d   # 3) 重启生效
# 4) 升级后复查：/api/setup/status 仍 initialized:true；必要时重跑 Quickstart 第 4 步补身份池
```

诊断一条龙：`curl $DOUYIN_API_BASE_URL/docs`（dtk 探活）→ `ls data/douyin/live_segments/douyin/`（分片观测）→ worker 日志 grep `recorder_sync_`（值守桥）与 `sessions_active`（ingest）。

前端监控台：`make web`（或 API_PORT=8010 make web 指定后端端口）——「监控」页平铺全部抖音账号：主页/直播间双路配置、视频监控与直播值守开关、在播/同步/会话/转录状态 30 秒自刷新；「安全设置」页管理防风控节流（派发错峰、发现翻页上限、代理池预留）。终端版看板：`make live-status`——每个值守直播间一行（主播/在播态/录制器同步/最近会话/分片与转录段数/最近活动），数据取自 DB + recordings.json + StreamCap 日志（零额外抖音请求）。

## 排障

| 症状 | 原因 | 处理 |
|---|---|---|
| `docker daemon 未运行` / compose FAIL | Docker Desktop 未启动 | 启动 Docker Desktop 后 `make doctor` |
| 端口冲突（5432/6379/9000/9001 被占） | 其他项目容器占用同端口 | `cp .env.example .env`，改 `POSTGRES_PORT`/`MINIO_CONSOLE_PORT` 及 `DATABASE_URL` 中端口后 `make bootstrap` |
| seed 报 relation 不存在 | 先跑了 `make seed` 未跑 `make migrate` | 先 `make migrate` 再 `make seed` |
| `FAIL .venv 缺失` | 未安装依赖 | `make setup` |
| API 报连接拒绝 / RuntimeError（数据库不可达） | compose 服务未启动或 `.env` 端口覆盖与实际不符 | `make doctor` 定位，对齐 `.env` 与 compose 端口 |
| 测试建库报 `permission denied to create database` | `radar` 角色无建库权限（自建 PG 而非 compose 时） | `ALTER USER radar CREATEDB;` 或改用 `make bootstrap` 的 compose 实例 |
| resolve-url 502 且消息含 `二进制不存在` | 本机无 yt-dlp CLI | `pip install yt-dlp` 或 `.env` 设 `YTDLP_BINARY=<路径>`；API 镜像已内置 |
| resolve-url 400 `不在白名单` | URL 主机未放行 | `.env` 配 `MEDIA_HOST_ALLOWLIST=<域名 CSV>` 后重启 |
| prepare 日志 `MEDIA_FFMPEG_FAILED` / `ffmpeg` 不存在 | worker 机器无 ffmpeg | `brew install ffmpeg`；API 镜像已内置 |
| 首次 ASR 转录长时间无输出 | 首次 transcribe 才从 HuggingFace 拉模型（medium 约 1.4GB），非卡死 | 等待即可；慢/失败见下一行 |
| ASR 模型下载失败/超时 | HF 网络不通（国内常见） | `.env` 设 `HF_ENDPOINT=https://hf-mirror.com` 后重试 |
| B 站/YouTube 报 412 或 "Sign in to confirm you're not a bot" | 平台反爬要求访客 cookie | 导出浏览器 cookie 为 Netscape 文件，`.env` 设 `YTDLP_COOKIES_FILE=<路径>` |
| YouTube 报 "Requested format is not available"（仅剩 storyboard） | YouTube PO Token 墙（媒体流需来源证明 token） | 暂不支持 YouTube 下载；需部署 bgutil POT provider（挂账 EPIC-03+/Plan #4） |
| 抖音单视频报 "Fresh cookies are needed" | Argus 设备指纹风控，补 cookie 也无效 | yt-dlp 上游不支持抖音过盾；抖音采集走 Plan #4 专属 adapter |
| 抖音账号无法定时发现 | yt-dlp 抖音无频道列表能力 | 注册具体视频链接；账号定时发现走 Plan #4（外部 dtk 服务，见「抖音 Quickstart」） |
| prepare 报 `douyin 未配置`（问题+原因+修复三段消息） | `.env` 缺 `DOUYIN_API_BASE_URL`（或 `DOUYIN_API_KEY`） | 填键后重启 worker；YouTube/B 站链路不受影响 |
| douyin 链路整体超时/连接拒绝 | 外部 dtk 栈未起或 migrate 失败 | `curl -fsS localhost:8080/docs` 探活；`docker compose -f infra/docker/docker-compose.douyin.yml ps` 看 dtk-migrate 是否 Exited(0)，日志定位 |
| dtk 任务 400 / `IDENTITY_POOL_EXHAUSTED`，或提示 cookie 失效 | 身份池为空、jar 过期或混入他域 cookie（Discussion #548 同类） | 重导新鲜 douyin-only jar（Quickstart 前置步+第 4 步）；`user_agent` 必须与 jar 来源浏览器一致 |
| StreamCap 无分片落盘 | 监控循环未激活（配置写入触发重启后需一次 UI 会话）/未开播/值守桥未写入 | 打开一次 http://localhost:5001；`ls data/douyin/live_segments/douyin/`；worker 日志 grep `recorder_sync_` |

### prepare 错误码速查（读自 `source_item.metadata_json.last_error`）

| code | 阶段 | 处理 |
|---|---|---|
| `RESOLVE_FAILED` | resolve | 查链接有效性/平台支持 |
| `DOWNLOAD_FAILED` | 媒体下载 | 网络/风控，稍后重试 |
| `MEDIA_FFMPEG_FAILED` | 音频标准化 | 查 ffmpeg 二进制与源文件完整性 |
| `ASR_FAILED` | 转录 | 查模型与资源（内存/磁盘） |
| `TRANSCRIPT_FAILED` | 段落校验 | 看 `last_error.message`（空段落/时间倒置/重叠超限） |
| `MEDIA_TOO_LONG` | 时长闸 | 超 `PREPARE_MAX_MEDIA_DURATION_SEC`，拆条或调大阈值 |

### 运维注记

- **ASR 引擎（mlx，Apple Silicon）**：`.env` 设 `ASR_PROVIDER=mlx` + `ASR_MLX_PYTHON`/`ASR_MLX_WORKER` 指向 voice-pro 的 venv_arm64（Python 3.12 + mlx-metal + mlx-whisper），radar 以子进程桥接、零新依赖。基准：whisper-medium 转录 13.4min 中文音频 **59s vs CPU faster-whisper 56min（~57x）**，模型 `mlx-community/whisper-medium` 已在本机 HF 缓存。模型质量与 faster-whisper medium 同级。
- **视频转录语义**：注册账号的首扫只采集历史视频**标题**（metadata `backfill` 标记），不自动转录；周期轮询发现的**新**视频仅当「视频监控」开（auto_poll）才自动转写；其余一律在监控台「视频库」手动点「转写」。

- **worker 低并发**：ASR 是 CPU 密集且长事务占用 DB 连接，`make worker-beat` 建议低并发（如 `--concurrency=2`）；高并发只会互相抢 CPU。
- **模型缓存**：本机 venv 运行走 `~/.cache/huggingface`（已与 `~/Project/voice-pro` 共用 Systran faster-whisper 模型，无需重复下载）；容器化 worker 需把该目录挂载为卷，否则每次重启重新下载模型。

`make reset-db` 为销毁性操作：清空全部本地数据卷并重建，不可恢复。

## 目录结构

```
apps/api/            FastAPI + SQLAlchemy 2.x (sync) + Celery
  app/core/          settings（pydantic-settings）
  app/db/            Base/mixin、session、models（14 张 PRD 表）
  app/domain/        枚举（Stance/Horizon 等 9 个）
  app/repositories/  数据访问层
  app/services/      编排与适配（media/、storage/、transcription/、preparation、discovery）
  app/api/v1/        REST 路由（source-items 等）
  migrations/        Alembic 迁移（ADR-0003：随 api 工程放置）
apps/web/            Vite + React + TS，vitest/oxlint
infra/docker/        Dockerfile.api（两阶段构建）
scripts/             seed_dev.py 开发种子
tests/               集成测试（radar_test 库，迁移后逐表截断）
docs/adr/            架构决策记录
```

## 已完成

- RAD-001~003：仓库基线、Makefile/pre-commit、CI（6 jobs）、API Dockerfile、docker compose 开发依赖（postgres/redis/minio）
- RAD-010~013：API 骨架与健康检查、领域枚举、Alembic + 14 张表迁移（含约束/级联/UTC 集成测试）、repository 层、开发种子数据
- RAD-020~023：媒体 Adapter 契约（yt-dlp 子进程，ADR-0007）、URL 白名单闸、手工解析/创建 API、Celery 账号发现 + beat 到期派发
- RAD-030~035：MinIO 存储服务、prepare 编排（字幕优先 + ASR 兜底，幂等行锁 + discovered 周期补扫）、ffmpeg 音频标准化、faster-whisper 转录（WhisperX flag 默认关）、transcript 持久化

## TODO

- EPIC-04+：观点抽取（chunk/LLM）、共识快照、前端界面、审核流等（见执行计划）

## 关键决策

| ADR | 决策 |
|---|---|
| [0001](docs/adr/0001-single-python-distribution.md) | 单 Python 发行包（api 为主工程，web 独立 npm） |
| [0002](docs/adr/0002-sync-sqlalchemy.md) | SQLAlchemy 用 sync（非 async），API/worker 共用 repository |
| [0003](docs/adr/0003-alembic-location.md) | 迁移目录放 `apps/api/migrations`，URL 运行时注入 |
| [0004](docs/adr/0004-extract-idempotency.md) | EXTRACT_VIEWPOINT 幂等键 `(source_item_id, prompt_version, extractor_version)` |
| [0005](docs/adr/0005-engine-pool-and-async-threshold.md) | 连接池参数显式化；sync→async 切换阈值量化留待实测 |
| [0006](docs/adr/0006-audit-log-write-deferral.md) | audit_log 只建表，写入服务推迟至 EPIC-05 |
| [0007](docs/adr/0007-ytdlp-subprocess-and-sync-adapter.md) | yt-dlp 走子进程 CLI；Adapter 契约同步（偏离执行计划 async 伪码） |
