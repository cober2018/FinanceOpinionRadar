# 财经观点质量基线

本目录用于冻结真实素材、记录人工标注资格，并对模型预测做可重复的离线评分。当前两条素材是真实转录，但标注只用于演示格式，尚未经过人工复核，不能得出正式准确率结论。

## 目录结构

- `sources.json`：数据集版本和素材清单。
- `transcripts/`：冻结转录，每段必须有唯一 `id`、起止时间和原文。
- `annotations/`：人工参考观点及资格状态。
- `predictions/`：冻结模型或演示预测。文件必须说明生成方式，不能把演示预测包装为线上结果。
- `adjudications/`：人工语义裁决；内容变化后旧裁决失效。
- `expected/`：评测报告输出，默认不提交运行产物。
- `thresholds.json`：正式验收门槛。

## 资格状态

| 状态 | 含义 | 可否用于正式质量指标 |
|---|---|---|
| `demo` | 只验证文件和评测流程，允许缺证据或缺人工签署 | 否 |
| `draft` | 已开始人工标注，尚未完成复核或穷尽性检查 | 否 |
| `human_verified` | 由实际人员签署，证据可定位，声明了标注是否完整 | 是 |

`annotator.name`、`reviewed_at` 只能填写真实信息。模型预标注、脚本生成或格式迁移不得自动变成 `human_verified`。

## 人工标注字段

每条观点必须有稳定的 `reference_id`，并按下面规则填写：

| 字段 | 标注规则 |
|---|---|
| `claim` | 忠实概括讲话内容，不补充原文没有的因果和确定性 |
| `statement_type` | `opinion`、`prediction`、`reported_claim`、`factual_claim`、`method`；当前生产模型尚不输出此字段，评测报告应显示未评估 |
| `speaker` | 实际表达者；主持人转述第三方时不得标成主持人自身观点 |
| `stance` | 对目标对象的方向；否定、反问和引用必须结合上下文判断 |
| `horizon` | 原文明确或可无歧义推导的期限；未提及用字段状态表达，不猜测 |
| `conditional` | 是否依赖前提；为真时 `condition_text` 必须保存关键条件原话 |
| `topic` / `entities` | 原文中的主题和对象；对象别名可规范化，但保留 `raw_name` |
| `evidence_segment_ids` | 支持观点所需的全部冻结转录段，不能为空且必须属于当前素材 |
| `evidence_references` | `human_verified` 必填；逐段保存 `segment_id`、原文 `excerpt`、`start_ms`、`end_ms`，且必须与冻结转录逐项一致 |

可选字段使用 `field_status` 表达：`present`、`not_mentioned`、`unclear`、`not_applicable`。`not_mentioned` 表示原文没有说，`unclear` 表示材料存在但无法可靠判断，`not_applicable` 表示该类陈述不需要此字段，三者不能混用。

正例：“如果成交量持续放大，未来一周可能反弹”必须保留“成交量持续放大”和“一周”。反例：只标“看多”，或把“有人认为会涨”记为讲话人本人的观点。

## 三层核验边界

- `transcript_support`：观点是否忠于冻结文字。
- `audio_video_verified`：冻结文字是否已对照原音视频。
- `external_fact_verified`：讲话人陈述的事实是否经过外部来源核实。

前一层通过不代表后一层通过。第一阶段正式语义指标只评价 `transcript_support`；其余状态必须随素材显示。

## 预测与人工裁决

预测文件必须记录 `run_id`、数据集版本、输入哈希、提示词和模型标识、全文/分块模式、预先计划的逻辑单元、每个单元最多三次尝试以及实际覆盖范围。模型调用失败、结构失败、部分处理和成功无观点必须分别记录。

人工裁决按 `prediction_id` 唯一对应预测，可选一个 `reference_id` 做一对一匹配，状态为 `pending`、`pass` 或 `fail`。`pass` 必须绑定一个 `reference_id`；已对齐但方向相反等语义错误可用带 `reference_id` 的 `fail`；无对应金标准或重复预测可用不带 `reference_id` 的 `fail`。文字相似度只生成候选；方向、期限等待测字段不能作为能否匹配的前置条件。`pass/fail` 必须记录真实审核人、时间，以及所绑定数据集、预测和标注哈希。

## 数据分组与正式覆盖

`development` 用于调试，`acceptance` 用于独立验收。相同作者、同一原视频的转载或切片必须放在同一组，避免信息泄漏。首轮正式验收至少需要：20 个真实素材、300 条人工观点、5 位人物、10 个主题，并覆盖直播、短视频和长视频。

当前缺口：2 个 `demo` 长视频，0 个 `human_verified` 素材，0 条正式人工观点；没有直播、短视频和独立验收组。数据收集和真实人审是后续业务工作，工具完成不会自动补齐这些缺口。

## 使用方式

离线演示评分（不连接数据库、不调用模型）：

```bash
.venv/bin/python scripts/evaluate_viewpoints.py \
  --dataset tests/golden \
  --predictions tests/golden/predictions/demo_legacy.json \
  --mode demo
```

正式评分把 `--mode formal` 与人工裁决文件一起提供；材料不足时退出码为 2，报告状态为 `not_evaluated`。只有显式增加 `--generate-predictions` 才调用模型；生成结果写入新的 run 目录，不覆盖历史文件。

评测脚本的退出码：0 表示正式通过或演示流程运行完成；1 表示完整评测未达标或最终处理不完整；2 表示正式材料/人审不足；3 表示输入非法或评测器错误。演示退出 0 仍然是 `not_evaluated`。
