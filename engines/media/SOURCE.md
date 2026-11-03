# engines/media 来源与版本

- 来源：Docker 镜像 `evil0ctal/douyin_tiktok_download_api:5.1.0`（本机镜像 ID `cc8a7ab15d0d`）内 pip 包 `dtk 5.1.0`（`/app/.venv/lib/python3.12/site-packages/dtk`，195 个 .py）。
- 提取方式：`docker create` + `docker cp`，仅删除 `__pycache__`/`*.pyc`，源码未改写（2026-11-03）。
- 许可与作者信息见 `vendor/dtk-5.1.0.dist-info/METADATA`。
- 上游为 Evil0ctal/Douyin_TikTok_Download_API 项目的定制打包；本仓库不宣称原创。

## 产品入口与装配（验收 A1，2026-11-03）

- `server.py` 是**精简同步引擎**：只装配雷达实际使用的两个端点
  （`douyin.content_detail` / `douyin.author_posts`），鉴权复用 dtk 的
  `X-API-Key → api_keys` 表；解析在请求内同步执行
  （`worker.registry.resolve → services.fetch.FetchService.fetch`）。
- **解除**的运行依赖：dtk `tasks` 表读写、`tasks:queue`/`tasks:signal` Redis 队列、
  `dtk-worker` 进程（compose 已移除该服务）、`submit_and_wait`/202/task_id、
  watchlist/archive/content_snapshots 旁路、console/MCP/TikTok 等其余表面。
  vendored 源码保留作库（解析/签名/身份池原样复用），不再整体装配。
- **保留**的运行依赖（必要身份/凭据/缓存）：`users`/`api_keys`（鉴权）、
  `identities`（加密身份池，`DTK_SECRET_KEY` 解密）、`proxies`、`settings`
  （启动时 `load_config` 快照）、`request_log`（fetch 内建可观测行）、
  Redis（响应缓存 `cache:*` + 调度租约/熔断 `sched:*`）。
- 雷达依赖面（不变，evidence/baseline-2）：`GET /api/v1/douyin/video`、
  `GET /api/v1/douyin/user/posts`（`apps/api/app/modules/content/media/adapters/douyin_client.py`），
  信封 `{"success", "data"|"error", "meta"}` + `X-API-Key` + `wait<=30`。
- 有意行为差异：`wait` 仍按 `api.max_wait_seconds` 校验，但同步执行总给出终态结果，
  不再出现 202/task_id；限流不叠加 dtk 的 Redis 固定窗口（引擎仅绑 127.0.0.1 服务雷达，
  客户端侧已有 download_gate 并发闸）。
- 隔离实测（2026-11-03，恢复旧 dev 栈身份数据）：真实上游取视频 447ms、
  user/posts 翻页 + cursor、重复调用 `cached=true`、
  `INVALID_PARAM`/`UNAUTHENTICATED` 信封、radar 未改动客户端直连全通；
  `tasks` 表零新增、Redis 无 `tasks:*` 键、栈内无 worker 进程。
