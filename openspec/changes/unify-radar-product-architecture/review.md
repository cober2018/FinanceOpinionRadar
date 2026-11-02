# 统一产品架构提案审查

## 2026-11-03 审查结论

方向符合已确认目标：复用成熟实现，整个项目按统一架构组织。首次审查发现以下三项设计缺口；用户随后授权更新 OpenSpec，现已完成文档修订及同一执行者的对应关系复核。下列“位置”保留修订前行号以说明原问题，修订后的依据使用章节和任务编号。严格格式校验不能替代运行验收；本记录没有独立审计、运行验证或生产检查结论。

### R1：模块归属明确，但跨模块调用与事务边界未明确

- 位置：`design.md:35-42`、`design.md:63-69`，`tasks.md:10-11`。
- 证据：提案划分了 content、insights、delivery，却没有规定共享记录的写入归属、调用方向以及谁提交跨模块事务。现有 `apps/api/app/services/video_summary_feed.py:153-175` 在来源或审核变更的事务提交前撤回旧事件；`:178-204` 在总结所属事务内生成 ready 事件。`apps/api/app/services/live_ingest.py:475-482` 的来源信息变化也会触发撤回。因此实际依赖不只是图中的单向数据流。
- 影响：各模块分别搬迁并自行提交，可能使总结、审核资格与对外事件不同步；仅按目录归位也不能阻止模块继续互相读写内部数据。
- 最小修订建议：在现有设计补一张关键数据的拥有者/写入口/调用方向表，明确 SourceItem、总结与 VideoSummaryEvent 的事务协调者、提交责任及现有锁顺序的保留方式。API 和任务入口继续复用业务处理，无需引入新消息总线或通用工作流。
- 修订与复核：design 2.1、2.2 增加拥有者/写入口表、workflows 协调、依赖方向、同一 session 提交和锁顺序；spec 的 Cross-module mutations 要求覆盖来源/审核撤回、生成后资格复核及失败回滚；tasks 1.2、2.2、4.2 对应冻结、迁移和验证。文档层面已补齐，代码效果待实施验收。

### R2：录制和弹幕的持续运行契约不足

- 位置：`design.md:65-69`、`design.md:94-101`，`tasks.md:13-14`。
- 证据：设计只说明引擎接收输入、返回结果或错误，并把生命周期和分片交付留在任务描述中。现有 `apps/api/app/worker/celery_app.py:19-26` 将弹幕作为长任务分队列；`services/danmaku/collector.py:96-120` 持有会话锁；`services/live_ingest.py:554-585` 按分片标识去重、等待写入稳定、记录恢复路径并累计转录偏移。这些行为不能只用一次请求/响应说明。
- 影响：替换旧录制目录和页面生命周期时，如果没有继承这些约定，可能提前消费未写完的文件、重启后重复摄入，或改变直播转录偏移和证据定位。
- 最小修订建议：分别写清录制/弹幕的启动、停止、退出和恢复责任，以及会话/分片标识、分片何时可消费、进度如何持久化、重试由哪层负责。明确复用哪些既有锁、去重和偏移规则，不要求额外建设通用进程管理平台。
- 修订与复核：design 2.3 明确启动、交付、摄入、停止和恢复责任；spec 的 Continuous engines 要求覆盖未完成/重复分片和异常退出接续；tasks 2.4、2.5、4.2 对应实现和验证。文档层面已补齐，代码效果待实施验收。

### R3：迁移与回退没有覆盖运行中的任务和存量文件引用

- 位置：`design.md:124`、`design.md:126-133`，`tasks.md:21-26`。
- 证据：方案已经要求避免新旧调度同时运行，也要求保留回滚产物，但没有说明如何交接排队/执行中的任务、未完成的直播分片以及投递状态。现有 Celery 显式任务名和队列见 `apps/api/app/worker/celery_app.py:19-26`；分片恢复路径被写进业务元数据（`services/live_ingest.py:574`）；推送已有 sent/dead/unknown 状态处理（`services/video_summary_push.py:116-176`）。移动目录或替换入口后，这些持久状态不会随源码自动迁移。
- 影响：新部署正常启动也可能留下无法消费的旧任务或无法读取的待处理文件；仅回退二进制和配置，不能证明新版本运行后的数据、任务仍能由旧版本接续。
- 最小修订建议：补充切换演练步骤：暂停新派发，排空或记录并接续在途任务，保留任务名/队列兼容，核对媒体及分片路径映射，确认只有一套调度和投递执行者；以有存量数据的隔离环境验证接续。明确回退条件和数据兼容范围，保留 unknown 投递的原处理语义，不能默认重新发送。本轮仅补设计建议，不执行生产切换。
- 修订与复核：design 的“存量接续与回退演练”覆盖清单、停止派发、旧消息/文件接续、唯一执行者及回退；spec 的 Migration 要求覆盖在途任务、投递状态和新写入后的回退；tasks 1.2、3.2、3.3、4.2 对应实现及演练。文档层面已补齐，切换/回退效果待实际演练。

### 已知待核实事项与非缺陷

- 来源代码的精确版本、必要依赖和可迁入范围尚未核实；提案已在任务 1.1 明确安排，因此不重复计为遗漏。完成核实前不能保证各外部产品能按预计方式拆出能力。
- 多个必要进程、数据库和第三方库不违背统一产品架构；不建议为了“一体化”强行合并所有进程或重写成熟算法。
- 审查轮只修改审查与进度记录；本次授权修订同步 proposal/design/spec/tasks、架构图及成果入口。未运行业务测试，也没有实现验收结论。

## 2026-11-02 原提案文档自检

## 范围与状态

本记录为当前提案的文档自检，不是独立代码审计。代码基线为本地 main `b013bfe`；没有业务代码迁移或运行验收，tasks.md 的 14 项实施任务全部未执行。

## 已核对项

| 检查 | 依据 | 结论 |
|---|---|---|
| 产品边界 | 用户确认与 proposal | 仅财经雷达，内容中心及量化平台保持独立 |
| “保留”的含义 | 用户进一步澄清与 design | 复用成熟功能和实现，全工程按统一目录归位，不保留旧工程结构作为约束 |
| 全项目覆盖 | design 目标目录与 tasks 2、3 | 包含前端、后端、任务、能力引擎和安装发布，非仅添加包装入口 |
| 运行归属 | spec 的业务状态、引擎及交付要求 | 业务流程由雷达掌握，可有多个必要进程，不借用旧项目环境和页面会话 |
| 兼容范围 | design 兼容章节与 spec | 保留当前已实现功能、数据、审核、时间和推拉契约，未夹带其他未确认提案 |
| 实施权限 | README、proposal、tasks | 提案待用户确认，未执行 apply 或生产操作 |
| 格式校验 | OpenSpec 严格校验 | 本变更通过；全量 7 项通过、0 项失败 |

## 尚待实际核实

来源工程的具体版本、可迁入代码和完整运行依赖需要任务 1.1 核实。现有代码迁移、安装、构建及独立运行需要完成对应实施任务后才能评价；本次不能声明架构已经实现或验证。

## 后续审阅与修复反馈

2026-11-03 已按用户指令完成 R1–R3 的 OpenSpec 文档修订与对应关系复核，14 项实施任务仍全部未完成。未新增独立需求目录、测试代码或业务功能，未执行 apply。文档缺口补齐不表示代码已修复或生产已验证；后续仍需用户确认完整提案后实施。

修订后校验：`openspec validate --all --strict --no-interactive` 为 7 项通过、0 项失败；当前变更 4/4 产物齐备，spec 共 9 条要求；`git diff --check` 通过。仅证明规范结构及文档检查通过。

## 2026-11-03 开发自检与交接（开发 Agent）

> 本节由开发 Agent 撰写，是开发自检记录，**不构成验收 Agent 的验收结论**。任务 4.3 的最终归档保持未完成，待原对话验收 Agent 验收。

### 代码位置与版本

| 项 | 值 |
|---|---|
| 工作区 | `/Users/mshengran/Project/FinanceOpinionRadar`（唯一继续维护的工作区，README 已标注） |
| 分支 | `codex/unify-radar-product-architecture`（基线 main `b013bfe`） |
| 提交链 | `b3a2251` 规范带入 → `6a20557` 基线冻结(1.1/1.2) → `c321070` 后端重组(2.1/2.2) → `aeceb8b` 四引擎迁入(2.3-2.6) → `d93d8cc` 前端拆分(3.1) → `0b8a3ac` 引擎构建切换(3.2) → 本次收尾提交（3.3/4.1 文档与检查） |
| 未提交改动 | 无（全部成果已提交；`cpa_api_tunnel.log`、`infra/launchd/*.plist` 按提示词保持未跟踪原状） |

### 任务状态（对照原 tasks.md）

- **1.1 / 1.2 完成**：`evidence/baseline-1-engine-sources.md`（四引擎来源/调用链/依赖冻结，docker 镜像实际提取核对）+ `evidence/baseline-2-contracts.md`（API/任务名/锁/事件/投递状态/文件契约冻结）。
- **2.1 / 2.2 完成**：后端按 design 1 目标布局重组（`bootstrap/`+`runtime/`+`modules/{workflows,sources,collection,content,insights,delivery}`）；原 104 文件按数据归属表归位（模型/仓储/服务/API/tasks 五层随域）；`modules/workflows.py` 承接原 `video_summary_feed.py` 的跨域事务协调（锁序 `lock_source_item → lock_event_order`、生成后指纹复核、撤回/ready 原子性函数原样保留）；参与域不自行 commit 的既有写法保持。
- **2.3-2.6 完成**：四引擎 vendor + 产品构建入口（`engines/{media,recording,danmaku,transcription}`，各含 SOURCE.md 来源/版本/许可说明）。
- **3.1 完成**：`App.tsx`（3620 行）按 `app/features/shared` 三层拆分（13 个 feature 文件 + 7 个 shared 模块），可见行为零改动。
- **3.2 完成**：Makefile `engines` 目标组；compose 三服务切换为产品内构建；编排契约（volume/端口/网络）不变。
- **3.3 完成（边界内）**：旧内部目录已无残留（`app/{api,core,db,domain,llm,repositories,services,worker}` 全部移除，无兼容转发）；`streamcap_keepalive.sh` 标注退役（生产仍在用旧路径，未授权不删不停）。
- **4.1 完成**：全量检查通过（详见 `evidence/implementation-verify-20261103.md` 汇总表；pytest 491 过 / ruff mypy 无新增 / tsc 0 / build+test 过）。
- **4.2 部分**：引擎接管与契约面已独立环境实证；存量副本完整演练未执行（见下）。
- **4.3 部分（按规范保持未勾选）**：文档回写已完成；最终验收与归档待验收 Agent。

### 规范对应（九条 → 实现入口 → 证据）

| # | 要求（spec） | 实现入口 | 证据 |
|---|---|---|---|
| R1 | whole project one architecture | 全仓目标布局（apps/api 三层 + engines/ + web 三层） | 目录树 + 提交链 |
| R2 | product modules own business flow | `modules/*` + `bootstrap/*` 装配、引擎无业务表 | c321070 diff |
| R3 | engines without old workspaces | `engines/*/SOURCE.md` + server/worker 入口 + Dockerfile | aeceb8b + 引擎运行证据 |
| **R1(审查)** | cross-module one transaction | `modules/workflows.py`（原 video_summary_feed 原样迁移，函数与锁序不变） | 基线对照 + test_review_and_history 等 491 项 |
| **R2(审查)** | continuous engines lifecycle | `engines/recording/server.py` 无头启动 + 会话/分片身份不变；danmaku 协议/队列不变 | 容器日志证据（监控循环自启） |
| **R3(审查)** | durable work handover & rollback | 任务名/队列/参数不变 + schema 零变化 + 文件引用零变化 + compose 回退（旧镜像记录于 SOURCE.md） | evidence 实施验证文档 |
| one product config/delivery | Makefile engines 组 + compose build 切换 | 0b8a3ac |
| behavior/identity compatible | 契约冻结清单逐项保留（baseline-2） | 491 项既有检查 |
| independence demonstrated | 检查汇总 + 引擎独立运行 | evidence/implementation-verify-20261103.md |

### 复现步骤（隔离环境）

1. `git checkout codex/unify-radar-product-architecture`
2. 后端：`make setup`（.venv + 前端依赖）→ `make test`、`make lint`、`cd apps/web && npm run build && npm test`
3. 引擎：`make engines`（构建三镜像）+ `make engine-transcription`（mlx venv）
4. 引擎栈：`DTK_SECRET_KEY=$(openssl rand -base64 48) docker compose -f infra/docker/docker-compose.douyin.yml up -d --build`（dtk-migrate 自动跑迁移；streamcap/douyinlive/douyin-api 均为本仓库构建）
5. 隔离数据：compose 默认使用 `data/douyin`（volume），无生产队列访问；凭据/cookie 按现有 `.env` 口径自行配置（不入库不入文档）。

### 待验收事项（区分必修与可选）

**必修（验收 Agent 判断是否阻塞归档）：**
1. 存量副本完整演练（design 迁移计划第 5/6 步 5 段演练）未执行——结构性连续性已核对（任务名/schema/文件引用零变化），但"有存量数据+在途任务"的运行级接续和回退需获准的数据副本。
2. 真实上游链路（douyin cookie/真实直播间/真实转写模型）未验证——凭据不在本环境。
3. `dispatch_pending_extractions`（insights）直查 SourceItem/SourceAccount 模型属既有跨域读——按 design 2.1 应走 content 查询契约，当前保留原实现（行为兼容优先）；是否要求收紧由验收定。

**可选优化（非缺陷，不应阻塞）：** 模型文件可进一步按域细拆；`monitoring_api` 的 llm_config 依赖方向；引擎镜像 CI 化。

### 边界重申

未合并 main、未推送远端、未发布生产、未停用/删除任何生产服务或数据；生产 dtk/streamcap/douyinlive 旧栈仍在原样运行。
