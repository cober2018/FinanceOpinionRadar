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
