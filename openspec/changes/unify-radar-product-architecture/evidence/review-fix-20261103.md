# 验收修复证据（2026-11-03，开发 Agent）

验收对象是 main `8e3acb9`。修复提交在本地 main，未推送、未对生产执行。

| 项 | 提交 |
|---|---|
| A2 workflows 事务协调 | `c42c9c0` |
| A4 新旧进程交接 | `4613c5e` |
| A1 同步媒体引擎 | `988bbfc` |
| A3 录制收尾与热重载 | `5ed1696` |

## 本轮实跑

- `pytest tests/unit tests/integration -q`：491 通过，两条既有弃用警告（Starlette/anyio）。退出码 0。
- `bash -n`：`scripts/deploy.sh`、`scripts/migrate_handover.sh`、`scripts/dev_up.sh` 通过。脚本没有对生产执行。
- `tests/unit/test_recorder_bridge.py`：11 通过。默认不再 `docker restart`。

### ruff（`ruff check apps/api tests scripts`）

验收提交 `8e3acb9` 实跑 52 条，当前 HEAD 实跑 48 条。净减的 4 条是删掉的 E501，不是忽略：

- `content/api_source_items.py` 两处（106 列、105 列）
- `content/live_ingest.py` 一处（125 列）
- `insights/summarizer.py:99`（111 列）

另外 4 条在 diff 里看起来像新的，只是原行号后移，正文长度不变：`live_rolling.py` 221→223、`live_ingest.py` 419→426 与 448→458、`delivery/open_api/routes.py` 的 B008 47→50。没有新的规则类别。

当前 48 条构成：E501×29、F841×9、B007×3、RUF059×3、B008×2、B023×2。

交接表写过「基线 E501 33 → 交接后 34，无新增类别」。本轮 E501 是 29，低于那两个数，但没有对 `b013bfe` 做逐行映射，所以不把计数下降写成逐项等价，也不称 lint 全绿。

### mypy（`mypy apps/api/app`）

`8e3acb9` 为 50 个错误 / 14 个文件，当前为 49 / 14。对照：

- 消失：`workflows.py:95` 的 `[var-annotated]`。
- 搬家：同一处 `dict(...)` 的 `[arg-type]` 从 `workflows.py:95` 到 `delivery/summary_events.py:35`。
- `insights/tasks.py` 的 6 条 `[union-attr]` 只是行号 +1。

交接文档写的「42=42、完全持平」和这次实跑对不上。以这次为准，不称逐项等价。

## A1（提交 988bbfc，细节在 engines/media/SOURCE.md）

精简同步引擎只挂 `GET /api/v1/douyin/video` 与 `/user/posts`。compose 去掉 `dtk-worker`。身份池、API key、Redis 缓存保留。SOURCE.md 记录了隔离环境里的真实上游取视频、翻页、缓存命中、错误信封，以及 `tasks` 表无新行、Redis 无 `tasks:*`。本文件不重复那组计时数字。

## A3 录制演练（本轮，独立容器，未碰仍在跑的 `ihmily/streamcap:v1.0.3`）

本机 ffmpeg 测试图案推到 `:5090`，引擎按 custom/.flv 拉流。

- 热重载 `segment_time` 120→90：日志 `config reloaded (changed:1)`，容器没重启。被换下的分片 ffprobe 为 mpegts，时长 28.09s，1282160 字节。
- 修过一个收尾缺陷：只等 `active_recorders` 变空会在 ffmpeg 刷盘前退出进程，约 7 秒的录制留下 0 字节文件。改为等 ffmpeg `returncode`。
- 修后：停之前文件仍是 0 字节，`docker stop` 之后同一文件为 mpegts，时长 6.02s，270532 字节。`engine-stop-report.json`：`graceful=true`、`drain_complete=true`、`was_recording=true`、`unfinished=[]`。再次启动日志为 `previous run stopped gracefully`。

这是合成流，不是真实直播间。

## 没有做的（4.2 仍未完成）

- `scripts/migrate_handover.sh` 未执行。没有对生产或任何真实队列做五段演练。
- 没有用旧版本进程读取新版本写入的队列、分片或事件，因此不能声称回退接续已验证。
- 真实直播间录制、真实弹幕房间、真实转写模型，本轮都没有复验。
- 审核 → 总结 → 推拉接收端的人工读回，本轮没有新做。集成测试绿不等于接收端读回。
