# OpenSpec 规范目录

## PRD / 需求背景

项目近期为公司内部 AI 内容中心提供准确、有出处的财经素材。需求背景见根目录 PRD 与 findings.md；代码变更先提出规范，经用户明确确认后实施，完成后归档。

## 目录结构

- `changes/`：尚未归档的变更提案，每个变更包含 proposal、design、specs 和 tasks。
- `specs/`：已实施并归档后合入的主规范，不能将待确认提案当作已实现能力。
- 当前变更：`changes/establish-finance-label-quality-baseline/`。

## 已完成

- 使用本机 OpenSpec 1.2.0 初始化项目，生成 `.codex/` 下的 Codex skills 和命令模板。
- 已生成第一批质量基线提案及验收规范，通过 CLI 严格校验。

## 待完成 / TODO

- 用户确认第一批提案后执行 apply。
- 实施并验证标签规范、离线评测和人工裁决材料，完成后 archive。
- 真实人工基准覆盖与质量验收按实际材料状态推进，不因工具完成而标成通过。

## 关键设计决策

- 当前先复用评测脚本及本地样本，不修改生产业务链路。
- 本地冻结输入、预测及人工裁决分别版本化；结构通过与语义准确分别报告。
- 用户提供的 AGENTS.md 要求“propose 产出后等待用户明确确认”，当前停在提案审阅阶段。

## 依赖 & 使用方式

- 依赖本机 `openspec` CLI。本次版本为 1.2.0。
- 已运行 `openspec init --tools codex`；新 slash 命令需重启 IDE 后加载。本会话使用项目 `openspec-propose` skill 和 CLI 创建同等产物。
- 查看状态：`openspec status --change establish-finance-label-quality-baseline`。
- 严格校验：`openspec validate establish-finance-label-quality-baseline --strict --no-interactive`。
- 状态输出的四类 artifact 完成，仅表示提案文件齐全；实施进度应看 tasks.md 中的复选框。
