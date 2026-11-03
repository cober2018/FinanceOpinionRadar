# engines/recording 来源与版本

- 来源：Docker 镜像 `ihmily/streamcap:v1.0.3`（本机镜像 ID `d9ca0298e743`）内 `/app` 工程源码（Python/Flet，ihmily/StreamCap 项目）。
- 提取方式：`docker create` + `docker cp`（2026-11-03）；仅删除 `__pycache__`、空 `logs/`、`downloads/`（运行时产物），源码未改写。
- 许可见上游 LICENSE（随源码保留在 vendor/streamcap）。
- 关键行为冻结：recordings.json 配置 schema（recorder_bridge 写入格式）、`<platform>/<author>/<date>/` 分片目录、TS 分片命名（前 14 位 YYYYMMDDHHMMSS）、loop_time_seconds 探测间隔、`check_live_on_browser_refresh` 配置键。
- 本产品改造点（相对上游唯一的入口差异）：新增 `server.py` 无头启动——以进程内虚拟 Flet 页面会话直接启动监控循环，替代"第一个浏览器会话连上才启动探测任务"的上游行为（原行为见 scripts/streamcap_keepalive.sh 注释：会话死亡会永久杀死监控循环）。录制核心、配置读写、分片交付逻辑零改动。

## 2026-11-03 复审修复补充

- **运行时二进制依赖**（对齐原镜像实测）：原镜像自带 node v20.20.2（douyin 取流 JS 签名运行时）与 ffmpeg 7.1.3；产品 Dockerfile 以 `apt nodejs`（trixie 20.x）+ `ffmpeg` 对齐。缺 node 会触发 UI 安装对话框并撞上游 bug，且取流期签名失败。
- **flet/TextButton 上游缺陷说明**：原镜像与 vendored requirements 均为 flet 0.85.0，`installation_manager.show_install_dialog` 的 `TextButton(text=…)` 在该版本即崩——**上游自带，非本产品构建漂移**；触发条件是"组件缺失"，补齐 node/ffmpeg 后对话框不再出现（2026-11-03 容器实测无 Traceback）。
- **依赖冻结**：`requirements.frozen.txt` = 原镜像容器内 `pip freeze` 全量 64 包逐字导出；vendor 的 requirements.txt 为 `>=` 浮动约束（streamget/flet 等会随构建时间漂移），镜像构建一律使用冻结清单。
- **分片写路径**：compose 分片挂载点必须为 `/app/downloads`（user_settings.live_save_path 的存量与默认值；详见 compose 内注释）。

## 生命周期（验收 A3，2026-11-03）

- `server.py` 在无头监控之外补了 design 2.3 的引擎侧契约，vendor 录制核心未改：
  - 每 30s 按 url 对账 `recordings.json`（新增 start_monitor、删除 stop+移除、参数漂移先停后换）。新增条目只在 `monitor_status` 为真时启动监控（vendor 的 start 会把 False 强翻 True 并立即监控）；漂移字段含 `segment_record`/`flv_use_direct_download`（翻转即换装）。`recorder_bridge.sync_live_monitors` 默认只原子写文件，不再 `docker restart`（`restart=True` 留给无热重载的旧外部镜像）。
  - SIGTERM/SIGINT：停新派发 → `request_stop`（vendor 对 ffmpeg 发 SIGINT）→ **等 ffmpeg 进程退出**（≤25s）再写 `<config_dir>/engine-stop-report.json`。不能用 `active_recorders` 变空当收尾完成：vendor 在 SIGINT 返回前就摘掉录制器，事件循环提前结束会杀掉还在刷盘的 ffmpeg，短录分片被截成 0 字节。
  - `was_recording` 取信号到达时的快照。`stop_recording` 会同步清掉 `is_recording`。
  - 启动时停止报告缺席 = 上次异常退出（未完成分片留在原处，交 radar live_ingest 稳定等待语义核对）。
- 隔离演练（本机合成 FLV → custom handler，独立容器，未碰仍在运行的 `ihmily/streamcap:v1.0.3`）：
  - 热重载 `segment_time` 120→90：日志 `config reloaded (changed:1)`，容器未重启；被换下的分片 ffprobe 为 mpegts、时长 28.09s。
  - 录制约 6s、停前文件仍为 0 字节时 `docker stop`：等 ffmpeg 退出后同一文件为 mpegts、时长 6.02s、270532 字节。报告 `graceful=true`、`drain_complete=true`、`was_recording=true`、`unfinished=[]`。再次启动日志为 `previous run stopped gracefully`。
