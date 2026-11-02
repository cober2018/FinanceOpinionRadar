# engines/recording 来源与版本

- 来源：Docker 镜像 `ihmily/streamcap:v1.0.3`（本机镜像 ID `d9ca0298e743`）内 `/app` 工程源码（Python/Flet，ihmily/StreamCap 项目）。
- 提取方式：`docker create` + `docker cp`（2026-11-03）；仅删除 `__pycache__`、空 `logs/`、`downloads/`（运行时产物），源码未改写。
- 许可见上游 LICENSE（随源码保留在 vendor/streamcap）。
- 关键行为冻结：recordings.json 配置 schema（recorder_bridge 写入格式）、`<platform>/<author>/<date>/` 分片目录、TS 分片命名（前 14 位 YYYYMMDDHHMMSS）、loop_time_seconds 探测间隔、`check_live_on_browser_refresh` 配置键。
- 本产品改造点（相对上游唯一的入口差异）：新增 `server.py` 无头启动——以进程内虚拟 Flet 页面会话直接启动监控循环，替代"第一个浏览器会话连上才启动探测任务"的上游行为（原行为见 scripts/streamcap_keepalive.sh 注释：会话死亡会永久杀死监控循环）。录制核心、配置读写、分片交付逻辑零改动。
