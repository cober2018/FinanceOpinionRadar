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
| 抖音账号无法定时发现 | yt-dlp 抖音无频道列表能力 | 注册具体视频链接；账号定时发现仅 YouTube/B 站 |

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
