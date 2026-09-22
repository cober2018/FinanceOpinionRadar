# scripts/

存放开发与运维辅助脚本，不属于应用运行时代码。

## 财经观点质量评测

`evaluate_viewpoints.py` 默认只读取本地冻结转录、预测和人工裁决，不连接业务数据库，也不调用模型。演示命令、标注协议、质量状态与退出码见 `tests/golden/README.md`。

```bash
.venv/bin/python scripts/evaluate_viewpoints.py \
  --dataset tests/golden \
  --predictions tests/golden/predictions/demo_legacy.json \
  --mode demo
```

正式运行改为 `--mode formal` 并通过 `--adjudications <文件>` 传入人工裁决。JSON、Markdown 和待审材料默认写入数据集的 `expected/`；文件名带时间和随机后缀，不覆盖历史报告。`--baseline` 只在数据集与评分版本一致时输出差异，不会绕过正式门槛。

只有显式使用 `--generate-predictions` 才读取环境中的 `LLM_BASE_URL`、`LLM_API_KEY` 和模型配置并调用现有 provider；短内容走现有全文提示词，超长内容走现有分块策略。调用前先原子写入完整逻辑单元计划，每次尝试后更新原始响应与解析结果；每单元最多三次尝试，首次失败不会因后续恢复而消失。正式评分会重建当前确定性计划并核对模型、提示词和输入哈希。生成预测不会调用生产抽取服务或写入业务库；未配置凭据时退出码为 3。

工具运行完成不等于质量通过。当前真实人工基准尚未建立，正式模式应返回 `not_evaluated`，并列出材料和人工审核缺口。
