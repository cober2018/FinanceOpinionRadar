# Plan #7：系统服务开关（开机自启 + 看门狗）

日期：2026-09-21　分支：main　状态：已完成

## 背景

用户此前明确"开发环境不做开机自启"（dev_up.sh 头注释记录），但随着直播值守成为长期能力，
组件静默死亡（已发生 3+ 次）成为实际运维负担。用户要求：**在系统设置页提供两个开关，
由用户自己控制**——既保留"不开机不启动"的默认，又给长开场景一键保障。

## 决策

- **U1 映射 launchd**：每个开关对应一个 LaunchAgent（模板 `infra/launchd/`，按仓库路径
  实例化到 `~/Library/LaunchAgents/`）：
  - `com.financeopinionradar.autostart`：RunAtLoad，登录时跑一次 `scripts/dev_up.sh up`
    （幂等拉齐 docker 依赖 + dtk + API :8010 + worker-beat；StreamCap keeper 是独立 agent，
    已由 Plan #6.5 落地，不在此范围）。
  - `com.financeopinionradar.watchdog`：KeepAlive 常驻 `scripts/radar_watchdog.sh`。
- **U2 看门狗不做冷启动**：首轮只记录基线不拉起，之后仅当组件「上一轮在、这一轮没了」
  才执行 `dev_up.sh up`。否则看门狗=变相自启，违背两开关分开的语义，也违背用户
  "有时不开机就启动项目"的偏好。手动停组件做维护会被 60s 内拉起——UI 提示先关看门狗。
- **U3 DB 存意图 + 启动对账**：`app_setting(key="runtime")` 存 `{"autostart": bool,
  "watchdog": bool}`（默认 false/false）；PUT 即应用 launchctl bootstrap/bootout；
  API 启动时按 DB 意图重新 apply（防 plist 被清理后状态漂移）。launchd 实际加载态
  以 `installed` 字段返回前端展示。
- **U4 默认全关**：与既有约定一致，用户在 UI 自行打开。
- **U5 UI 位置**：设置页新增「系统服务」卡（LLM/青果代理/安全卡同级），
  复用 `label.switch` 滑动开关。

## 交付物

- `apps/api/app/services/runtime_control.py`：get/put/sync_runtime_on_startup +
  launchctl 封装（模板渲染、加载态探测、bootstrap/bootout）
- `apps/api/app/api/v1/monitoring.py`：GET/PUT `/settings/runtime`
- `apps/api/app/main.py`：lifespan 启动对账（失败仅告警不阻断启动）
- `infra/launchd/com.financeopinionradar.autostart.plist` / `.watchdog.plist`（`__REPO_DIR__` 模板）
- `scripts/radar_watchdog.sh`：60s 轮询 pg/redis/api/worker，掉线才拉起
- `apps/web/src/App.tsx`：`SystemServiceCard` + `RuntimeSettings` 类型
- 测试：`tests/integration/test_runtime_settings.py`（launchctl 调用 monkeypatch）

## 实现记录

- 门禁：ruff / mypy（本次文件零新增告警，历史 8 个第三方 stub 报错维持原状）/
  pytest 377 通过、1 失败（`test_prepare_task_runs_pipeline`，在 HEAD 上 stash 掉本次
  改动后复现，属既有问题，疑与并行进行中的 live_ingest 改动相关）；web build + oxlint 通过。
- 手动验证：PUT autostart=true → agent 激活且 RunAtLoad 跑完 dev_up.sh up（日志五组件
  全绿）→ PUT false 卸载干净。watchdog=true → 基线记录 → 手杀 API → 60s 巡检发现掉线
  → dev_up.sh up 自动复活。验证后两开关复位为关（DB 意图 false/false，agent 已卸载）。
- 测试基建修复：`_constants.ALL_TABLES` 漏了 app_setting/deleted_item_ref/entity_candidate，
  集成测试清理不彻底导致跨测试残留（本次 app_setting 残留暴露）；已补齐。
- 教训：launchd 环境 PATH 不含 brew（plist 需补 EnvironmentVariables.PATH，同 Plan #6.5）。
- **回归修复（同日）**：launchctl bootout 会 SIGTERM 整个进程组——关闭看门狗时把它
  revived 的 API 一并杀掉（nohup/disown 只防 SIGHUP）。dev_up.sh 改为 os.setsid()
  新会话启动 API/worker；复测：看门狗复活 API → 关看门狗 → API 存活（health 200）。
