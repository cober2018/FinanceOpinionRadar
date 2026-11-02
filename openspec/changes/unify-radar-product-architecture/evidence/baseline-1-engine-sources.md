# 基线冻结 1：能力引擎来源与调用链（任务 1.1）

冻结时刻：2026-11-03，分支 `codex/unify-radar-product-architecture`，基线 main `b013bfe`。本记录为实施依据，来源以实际运行镜像/目录为准。

## 引擎 1：媒体解析/发现（dtk）

| 项 | 冻结值 |
|---|---|
| 来源 | Docker 镜像 `evil0ctal/douyin_tiktok_download_api:5.1.0`（本机镜像 ID `cc8a7ab15d0d`，814MB） |
| 包形态 | 镜像内 `/app/.venv` 安装的 pip 包 `dtk 5.1.0`（195 个 .py；包结构 platforms/signing/transport/core/media/identity/api/…），非上游仓库原始布局，为本机实际运行版本 |
| 雷达调用面 | 仅两个 GET（`apps/api/app/services/media/adapters/douyin_client.py`）：`GET /api/v1/douyin/video?aweme_id=&wait=30`、`GET /api/v1/douyin/user/posts?sec_user_id=&cursor=&count=&wait=30`；鉴权 `X-API-Key`；信封 `{"success", "data"|"error"}`；`wait` 上限 30s，202=超时语义 |
| 服务端入口 | 包内 `dtk/api/routes/content.py`：`video`（L424）、`user_posts`（L693） |
| 独立栈依赖 | 自带 postgres（timescaledb digest pin）+ redis 任务体系、`DTK_SECRET_KEY` 凭据主密钥、cookie jar、web 管理页（`/app/web/dist`）——统一化需评估剥离，业务任务归雷达 Celery |
| 关键依赖 | fastapi/httpx/pydantic/sqlalchemy[asyncio]/asyncpg/redis/structlog/wreq<1.0（签名传输）等，见 dist-info METADATA |
| 运行态 | 容器 `douyin-api`（同镜像，Exited 0）、`dtk-worker`（Exited 137）、`dtk-postgres`/`dtk-redis`（Exited 0）；编排 `infra/docker/docker-compose.douyin.yml` |
| 雷达侧耦合 | `douyin_client.py`（HTTP+异常映射）、`discovery.py`/`preparation.py`（业务任务）、`download_gate.py`（dtk 出口闸 `dtk` 键） |
| 迁移目标 | `engines/media`：以镜像内 `dtk` 包为来源冻结版本；保留两 GET 端点信封兼容，业务任务并入雷达任务系统；剥离评估在任务 2.3 实施时记录 |

## 引擎 2：直播录制（StreamCap）

| 项 | 冻结值 |
|---|---|
| 来源 | Docker 镜像 `ihmily/streamcap:v1.0.3`（本机镜像 ID `d9ca0298e743`，2.65GB） |
| 包形态 | 镜像内 `/app`：`main.py` + Python 工程（poetry，`requirements.txt`），含 Web UI（6006）与录制核心 |
| 雷达调用面 | `recorder_bridge.py`：diff 期望配置→原子写 `recordings.json`（`RECORDER_CONFIG_PATH` bind）→ 无变化不扰动；有变化 `docker restart streamcap`（配置不热重载）；**UI 会话激活残留**（`recorder_activation_required` 警告） |
| 文件契约 | 分片落 `${LIVE_SEGMENTS_DIR}`（bind `/app/downloads`），目录模板 `<platform>/<author>/<date>/`，TS 分片；`recording_dir` 由录制器物化回写（diff 剔除 `rec_id`/`recording_dir` 易变字段） |
| 身份语义 | `rec_id = uuid5(NAMESPACE_URL, "radar://streamcap-recording", url)` 确定性；分片文件名序号前 14 位=本机时区 YYYYMMDDHHMMSS（live_ingest `_index_start_utc` 同源解析） |
| 运行态 | 容器 `streamcap` **Up 4 days（生产在跑，不得扰动）** |
| 迁移目标 | `engines/recording`：镜像内源码为冻结版本；录制生命周期改为本产品进程直接启动（无 UI 会话激活前提）；保留 config schema 与分片目录/命名语义保证文件连续性 |

## 引擎 3：直播弹幕（douyinLive）

| 项 | 冻结值 |
|---|---|
| 来源 | `ghcr.io/jwwsjlm/douyinlive:v2.2.1`（compose pin；**本机无此镜像**，容器不在本机——生产弹幕服务主机待确认，镜像提取在任务 2.5 执行） |
| 雷达调用面 | `services/danmaku/collector.py`：`ws://127.0.0.1:1088/ws/<room_id>` 订阅（`DANMAKU_WS_BASE_URL`）；30s 文本 `"ping"` 保活约定；`ROOM_NOT_FOUND` 系统消息判别 |
| 雷达侧自有语义 | pg advisory lock `907300000+item_id`（session 级单飞）、jsonl 档案 `<sink>/<date>/<item_id>.jsonl` envelope v1（`{v, received_at, msg|raw}`）、心跳 `os.utime`、退出原因 `session_closed/max_duration/room_not_found`、重连 15s 由采集循环负责 |
| 迁移目标 | `engines/danmaku`：v2.2.1 源码（Node.js）入产品构建；雷达 collector 协议不变 |

## 引擎 4：转写（faster-whisper / MLX）

| 项 | 冻结值 |
|---|---|
| 来源 | 本地工程 `/Users/mshengran/Project/voice-pro`（`abus-aikorea-voice-pro/app/abus_asr_mlx_whisper.py` worker + `venv_arm64`：Python 3.12 + mlx-metal + mlx-whisper 0.4.x） |
| 雷达调用面 | `services/transcription/mlx_whisper.py` 子进程桥：`ASR_MLX_PYTHON` + `ASR_MLX_WORKER` 配置路径，argv 传 `--audio/--model/--lang/--output-json`，输出 JSON `{language, model, segments[{start,end,text}], elapsed_s}` |
| provider 契约 | `services/transcription/contracts.py`：`TranscriptionError/TranscriptResult/TranscriptSegmentResult`；`__init__.py` provider 选择 |
| 迁移目标 | `engines/transcription`：worker 脚本+依赖清单+安装脚本入产品（`mlx/`），`ASR_MLX_PYTHON/WORKER` 指向产品内 venv；faster-whisper 路径保持在雷达主 venv |

## 与本产品的边界（保持不变）

- 内容中心、量化平台为独立上下游产品，不在迁入范围。
- 雷达业务侧已自有：discovery/preparation/download_gate/proxy 池、live_ingest 分片去重/偏移/恢复、danmaku collector/ingest、transcription provider 契约、审核总结与推拉交付。引擎只提供能力，不持业务真相（design 决策 2）。
