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

`make reset-db` 为销毁性操作：清空全部本地数据卷并重建，不可恢复。

## 目录结构

```
apps/api/            FastAPI + SQLAlchemy 2.x (sync) + Celery
  app/core/          settings（pydantic-settings）
  app/db/            Base/mixin、session、models（14 张 PRD 表）
  app/domain/        枚举（Stance/Horizon 等 9 个）
  app/repositories/  数据访问层
  app/services/      编排与媒体适配（media/contracts+adapters+factory、discovery）
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

## TODO

- EPIC-03+：媒体下载/字幕/ASR、观点抽取、共识快照、前端界面、审核流等（见执行计划；EPIC-03 首任务须满足 prepare_source_item 幂等 + 存量 discovered 补扫）

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
