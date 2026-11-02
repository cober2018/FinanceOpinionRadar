# engines/media 来源与版本

- 来源：Docker 镜像 `evil0ctal/douyin_tiktok_download_api:5.1.0`（本机镜像 ID `cc8a7ab15d0d`）内 pip 包 `dtk 5.1.0`（`/app/.venv/lib/python3.12/site-packages/dtk`，195 个 .py）。
- 提取方式：`docker create` + `docker cp`，仅删除 `__pycache__`/`*.pyc`，源码未改写（2026-11-03）。
- 许可与作者信息见 `vendor/dtk-5.1.0.dist-info/METADATA`。
- 雷达依赖面：`GET /api/v1/douyin/video`、`GET /api/v1/douyin/user/posts`（`apps/api/app/modules/content/media/adapters/douyin_client.py`），信封 `{"success", "data"|"error"}` + `X-API-Key` + `wait<=30`。
- 运行前提：引擎自带的 postgres（凭据/身份池）与 redis（任务队列），由产品编排 `infra/docker/docker-compose.douyin.yml` 统一管理（已在本产品内）。
- 上游为 Evil0ctal/Douyin_TikTok_Download_API 项目的定制打包；本仓库不宣称原创。
