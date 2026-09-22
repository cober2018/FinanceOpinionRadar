# OpenSpec 规范目录

## PRD / 需求背景

项目近期为公司内部 AI 内容中心提供准确、有出处的财经素材。需求背景见根目录 PRD 与 findings.md；代码变更先提出规范，经用户明确确认后实施，完成后归档。

## 目录结构

- `changes/`：尚未归档的变更提案，每个变更包含 proposal、design、specs 和 tasks。
- `specs/`：已实施并归档后合入的主规范，不能将待确认提案当作已实现能力。
- `changes/archive/2026-09-22-establish-finance-label-quality-baseline/`：已完成的第一批财经标注质量基线变更。

## 已完成

- 使用本机 OpenSpec 1.2.0 初始化项目，生成 `.codex/` 下的 Codex skills 和命令模板。
- 第一批质量基线已完成实施和归档，主规范位于 `specs/finance-label-quality-baseline/spec.md`，全量严格校验通过。

## 待完成 / TODO

- 真实人工基准覆盖与质量验收按实际材料状态推进，不因工具完成而标成通过。

## 关键设计决策

- 当前先复用评测脚本及本地样本，不修改生产业务链路。
- 本地冻结输入、预测及人工裁决分别版本化；结构通过与语义准确分别报告。
- 用户明确确认后才进入实施；工具完成和业务质量验收保持两个独立状态。

## 依赖 & 使用方式

- 依赖本机 `openspec` CLI。本次版本为 1.2.0。
- 已运行 `openspec init --tools codex`；新 slash 命令需重启 IDE 后加载。本会话使用项目 `openspec-propose` skill 和 CLI 创建同等产物。
- 严格校验：`openspec validate --all --strict --no-interactive`。
- 已归档变更中的 tasks 全部完成；当前真实业务质量仍需补齐人工基准后再评估。
