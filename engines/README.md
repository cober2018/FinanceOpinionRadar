# engines/ — 产品自有能力引擎

| 引擎 | 来源（版本冻结见各自 SOURCE.md） | 进程入口 | 运行前提 |
|---|---|---|---|
| media | dtk 5.1.0（镜像内 pip 包原样 vendor） | `uvicorn engines.media.server:app` | 自带 postgres+redis（产品编排管理）；启动前 `dtk migrate` |
| recording | StreamCap v1.0.3（镜像内 /app 源码原样 vendor） | `python engines/recording/server.py`（无头，无需浏览器会话） | ffmpeg；STREAMCAP_CONFIG_DIR 指向产品配置目录 |
| danmaku | jwwsjlm/douyinLive v2.2.1（上游 tag 源码） | `go build ./cmd/...` 后运行二进制（`--port 1088`） | 无（本地 a_bogus 签名） |
| transcription | voice-pro 的 abus_asr_mlx_whisper.py（原样复制） | worker 子进程（由雷达 `mlx_whisper.py` 桥接调用） | `bash mlx/setup.sh`（Apple Silicon） |

原则（design 决策 2/3）：引擎只提供能力，不持业务真相；雷达业务表/任务状态全部在 `apps/api`。复用代码的来源、版本与许可说明在各自 SOURCE.md，不宣称为原创。
