# 配置进度

- 当前状态：连接器已创建，ChatGPT 页面显示已连接账户。
- 已完成：固定域名准备、当前工作区连接器创建、连接授权、本地安全检查。
- 下一步：绑定 `FinanceOpinionRadar` ChatGPT 项目，完成工作区身份和文件读取验证。

## 2026-09-22 产品矩阵评估进度

- 已确认目标：公司内部生产研究使用；非结构化数据清洗与标注是主要瓶颈；准确性优先、允许较慢；三个月首先服务 AI 内容中心。
- 已完成：只读核对清洗、抽取、审核、标签归一、质量评估、证据留存和下游交付相关代码；结合两项并行代码核对及一次产品边界审阅形成建议。
- 当前阶段：产品能力评估完成，证据、建议顺序及验收边界见 findings.md；README 已同步本次目标及待完善事项。
- 后续开发尚未立项。若用户要求实现，按项目要求先完成 OpenSpec propose 并经确认后再 apply。
- 本轮仅更新 README 和三个流程记录，未修改业务代码、未生成测试、未执行服务或模型评测；当前线上准确率与实际清理配置未核验。

## 2026-09-22 第一批 OpenSpec 提案进度

- 用户已授权继续下一步；先落实财经素材标注质量基线。
- 已确认本机 OpenSpec 1.2.0 可用；项目此前未初始化，本会话无 `/opsx:propose` 入口。
- 已通过 `openspec init --tools codex` 建立项目规范目录及本地 Codex 工作流；新 slash 入口需要重启 IDE 后加载。本轮使用已安装 CLI 与生成的 propose 工作流等价完成规范产物。
- 已建立变更 `establish-finance-label-quality-baseline`，完成 proposal、design、质量基线 spec 与 tasks，OpenSpec 严格校验及独立审阅均通过。
- 已同步根 README、规范目录说明、变更入口与流程记录。未执行 apply，未修改业务或评测代码。
- 独立审阅指出状态优先级和重试计数两处歧义；已统一主状态/退出码优先级，明确逻辑单元、首次尝试指标及最多三次尝试，复核通过。
- 当前状态：等待用户对本提案的明确确认。4/4 规范产物齐备仅表示提案完成，tasks 中实施项仍全部未执行；未 archive。

## 2026-09-22 实施进度

- 用户已明确确认本提案及 98% 首轮语义精确率门槛。
- 已按 executing-plans 在 task_plan.md 写出 `///` 实施步骤，OpenSpec 任务 1.1 完成。
- 下一步：白名单提交已确认规范到功能分支，并创建隔离工作区；`backups/` 不纳入本任务。
- 隔离工作区已创建于 `/Users/mshengran/.codex/worktrees/finance-label-quality/FinanceOpinionRadar`；现有仓库没有专门的 evaluation 测试文件。
- 已完成标注手册、两条 demo 资格迁移、来源及转录哈希、正式覆盖要求和实际缺口；OpenSpec 任务 1.2–1.4 完成。
- 已完成离线评测改造：默认不导入数据库或模型代码；显式生成模式复用现有 provider、提示词和全文/分块策略，最多三次尝试并保留原始输出。
- 已完成人工裁决、哈希失效、诚实分母、独立验收资格、来源分组、逐条差异、JSON/Markdown 报告及退出码。
- 静态检查通过：Ruff、Mypy、Python 编译和 `git diff --check`。
- 真实 demo 离线运行退出 0、状态 `not_evaluated`；同批正式运行退出 2、状态 `not_evaluated`。两次重复评分的 `results` 完全一致且报告未互相覆盖。
- 临时扰动副本已验证：方向相反进入方向分母并计错；无证据和重复项保留在精确率分母；pending 与过期裁决不输出完整精确率；重试恢复不抹去首次结构失败；最终处理失败优先返回 failed/1；零分母为不适用。临时材料未进入仓库基准。
- 未配置模型凭据的显式生成入口返回输入无效/3，没有产生预测文件；实际模型生成质量未在本轮声称已验证。
- 两轮代码审阅发现的输入 schema、调用前计划持久化、原始模型响应、人工裁决绑定、复核材料上下文、来源分组指标、错误报告和正式 provenance 问题均已修复；正在做最终复核。
- 严格临时正式样本验证：完整模型/提示词/计划 provenance、人工裁决和门槛全部满足时为 `passed/0`；同一计划停在 `planned` 且零次尝试时为处理不完整 `failed/1`。
- 变更文件的 Ruff、Mypy、编译检查通过；provider 既有单元测试 5/5 通过。全仓 `make lint` 只剩本批未改的 `tests/integration/test_discover_tasks.py:98` 既有未使用 import，本批不扩大范围修复。
- code-reviewer 与 python-reviewer 最终复核均通过，无剩余高风险问题。
- 已逐项核对规范、实现与验证证据：质量评测工具完成；当前只有 2 条 demo、无合格人工签署与独立验收覆盖，业务质量状态仍为 `not_evaluated`。
- 最终复核完成；OpenSpec 已同步主规范并归档至 `openspec/changes/archive/2026-09-22-establish-finance-label-quality-baseline/`。业务人审样本缺口继续保留为 TODO。
