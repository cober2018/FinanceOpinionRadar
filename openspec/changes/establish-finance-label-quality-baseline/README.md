# 财经素材标注质量基线

## PRD / 需求背景

建立公司内部财经素材标注质量基线，明确标签与证据规范，隔离演示样例和人工基准，修正离线质量评测口径。

## 目录结构

- [proposal.md](proposal.md)：范围、目标、影响及确认边界。
- [design.md](design.md)：数据与评分契约、建议门槛、流程图及迁移方式。
- [specs/finance-label-quality-baseline/spec.md](specs/finance-label-quality-baseline/spec.md)：可验收要求与场景。
- [tasks.md](tasks.md)：确认后的实施任务，当前均未实施。

## 已完成

- 提案及四类规范产物已生成，通过 OpenSpec 严格校验与独立审阅。

## 待完成 / TODO

- 用户明确确认提案，包括新增语义精确率至少 98% 的建议门槛。
- 按 tasks.md 实施、验证和归档。
- 内部人员对真实素材的人审签署与完整基准建设按实际进度记录；当前不宣称质量已达标。

## 关键设计决策

- 首批只改造本地评测及标注材料，不改变线上抽取、审核和数据留存。
- 默认离线评分，显式生成预测才调用模型；不连接业务数据库。
- 模型自报置信度、文字相似及证据位置存在都不能代替人工语义判断。

## 依赖 & 使用方式

在仓库根目录运行 `openspec status --change establish-finance-label-quality-baseline` 查看规范产物状态，运行 `openspec validate establish-finance-label-quality-baseline --strict --no-interactive` 校验。当前不得执行 apply；待用户明确确认后开始实现。
