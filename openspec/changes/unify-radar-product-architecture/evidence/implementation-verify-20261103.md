# 实施验证证据（任务 4.1/4.2）

实施日期：2026-11-03。分支 `codex/unify-radar-product-architecture`。基线 main `b013bfe`。

## 检查结果汇总（4.1）

| 检查 | 命令/目录 | 基线 b013bfe | 实施后 | 结论 |
|---|---|---|---|---|
| 后端测试 | `.venv/bin/python -m pytest tests/` | （既有套件） | **491 passed, 2 warnings** | 全绿，无删改断言 |
| ruff | `ruff check apps/api tests scripts` | 84 违例（E501×33/I001×15/F401×15/…既有） | 53 违例（E501×34/F841×9/B007×3/RUF059×3/B008×2/B023×2/I001×2 等，全部为基线同类既有项） | 无新增类别，总量下降 |
| mypy | `mypy apps/api/app` | 42 errors / 9 files | 42 errors / 9 files | 完全持平（文件迁移所致的同组错误） |
| 前端 tsc | `npx tsc -b`（apps/web） | 0 | **0 errors** | 拆分后类型完整 |
| 前端构建 | `npm run build` | ✓ | ✓（122ms，产物正常） | 通过 |
| 前端测试 | `npm test`（vitest） | 1 passed | **1 passed** | 通过 |
| eslint(oxlint) | `npm run lint` | 18 warnings / 0 errors | 13 warnings / 0 errors | 无回归 |
| OpenSpec | `openspec validate --all --strict` | 7 passed | 见 review.md 回执 | 通过 |

## 引擎独立运行证据（4.2 第一层：引擎接管）

| 引擎 | 验证 | 证据 |
|---|---|---|
| recording（StreamCap v1.0.3 vendored） | 产品镜像 `radar-engine-recording` 构建成功；容器内**无浏览器会话**直接启动监控循环：`Initializing periodic live check task with interval: 60s` + `Starting periodic live check background task` + 60s 存活心跳；对假房间发出真实上游探测（`fetch_stream` 记录 `Live URL: https://live.douyin.com/000000000000`，无 cookie 下预期失败并继续循环） | 容器日志（2026-11-03 会话留存于本记录）；宿主机同栈复测一致 |
| danmaku（douyinLive v2.2.1 vendored 源码） | `go build` 成功；进程启动 `WebSocket 服务启动成功 addr=ws://127.0.0.1:11088`；房间状态接口返回契约信封 `{"ok":false,...,"error":{"code":"upstream_unverified"}}`（无 cookie/假房间的正确语义）；容器镜像 `radar-engine-danmaku` 构建成功且同样响应 | HTTP 读回 |
| media（dtk 5.1.0 vendored） | 镜像 `radar-engine-media` 构建成功（依赖按镜像内实测版本逐包 pin：`requirements.frozen.txt`）；容器内 `dtk migrate` 到 head（0009）；容器内 uvicorn 启动后**两个雷达契约端点**均返回与 `douyin_client.py` 解析逻辑一致的信封：`{"success":false,"error":{"code":"UNAUTHENTICATED",...}}`（无凭据的正确拒绝） | HTTP 读回 |
| transcription（voice-pro worker 原样复制） | `mlx/worker.py` 与来源 `abus_asr_mlx_whisper.py` **diff 为空**；`setup.sh` 提供产品自有 venv 安装；argv/输出 JSON 契约不变（`test_mlx_whisper.py` 在 491 项中通过） | diff + 既有单测 |

## 兼容与接续核对（4.2 第二层：结构性连续性）

- **Celery 任务名/队列零变化**：`bootstrap/celery.py` include 指向的 5 个任务模块内任务名逐一与基线 `worker/tasks.py` 相同（显式 `name=` 保留）；旧队列消息以同名任务派发可被新 worker 消费。`test_worker_task_registry.py` 等注册面检查通过。
- **DB schema 零变化**：模型文件仅迁移位置，未改任何表/列/约束；**未新增 Alembic 迁移**。新旧版本可读同一数据库（回退与新写互操作的结构前提）。
- **媒体/文件引用零变化**：`LIVE_SEGMENTS_DIR`/`DANMAKU_SINK_DIR`/storage URI/分片命名/弹幕 envelope v1 语义全部保留（代码平移不改契约，`test_live_ingest_flow`/`test_danmaku_*` 通过）。
- **对外 API 装配序不变**：`bootstrap/api.py` 按原 `api/v1/__init__.py` 注册序装配；`/open/v1/*` 路由原样迁移；审计中间件保留。
- **编排切换**：`infra/docker/docker-compose.douyin.yml` 三个服务从外部 pin 镜像改为 `build: engines/*`（镜像内源码 vendor），volume/网络/端口契约不变；`docker compose config` 校验通过。

## 未验证项（如实）

1. **真实上游链路**：douyin 解析/录制/弹幕的真实 cookie 与真实直播间未在本环境使用（凭据属运营数据）——三个引擎对真实上游的完整成功路径未验证；契约面与失败路径已验证。
2. **存量数据隔离演练**：design「存量接续与回退演练」的 5 步在"含真实存量任务/活跃直播/投递状态"的副本上未完整执行（需获准的数据副本与停写窗口）。结构性连续性已由上节核对覆盖；完整演练列为待验收事项。
3. **旧版本读回新写入**：schema 无变化的结构核对已完成；逐字段运行级回退演练未执行（同上）。
