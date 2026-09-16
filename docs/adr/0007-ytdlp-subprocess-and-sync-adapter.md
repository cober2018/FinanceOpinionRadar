# ADR-0007: yt-dlp 子进程调用与同步 Adapter 契约

日期：2026-09-17（Plan #2 EPIC-02，/autoplan 三相审查 C1/C2）｜ 状态：已接受

## Context

RAD-020/021 要求定义媒体来源 Adapter 契约并实现通用适配器。执行计划的 RAD-020 伪码写的是 `async def`，与既有技术决策（ADR-0002 sync SQLAlchemy + sync FastAPI 端点）和 PRD 5.3 的同步契约签名相互矛盾；yt-dlp 的调用方式也有"子进程 CLI"与"Python 库内嵌"两条路。

## Decision

1. **同步 Protocol**：`MediaSourceAdapter` 四方法（discover/resolve/fetch_subtitle/download）全部同步。理由：PRD 5.3 本身就是同步签名；sync 栈下 async 只能靠 to_thread 伪装；yt-dlp 子进程天然阻塞。真异步切换挂 ADR-0005 的量化阈值，届时只动 adapters + 调用方。
2. **子进程 CLI 而非 Python 库**：`subprocess.run(argv, timeout=...)` 整体可杀、崩溃隔离（yt-dlp 崩不拖垮 API 进程）、版本由 Docker 镜像钉住（`yt-dlp>=2026.3.17`）；测试注入假二进制路径，CI 零外网。Python 库三条都不满足。
3. **安全边界**：派生子进程前过 `url_guard`（scheme∈{http,https} + 主机白名单 + 禁 userinfo），argv 列表直传禁 shell，stderr 截尾进异常消息。

## Consequences

- 运行镜像必须含 yt-dlp 二进制（Dockerfile 层1 已装；本地开发用 PATH 上的 yt-dlp，`YTDLP_BINARY` 可覆盖）。
- 平台改版坏掉时升级镜像重 build 即可，不碰应用代码。
- EPIC-03 的 fetch_subtitle/download 沿用同一子进程封装（`YtDlpProcess.run_json`），契约占位方法届时替换 NotImplementedError。
