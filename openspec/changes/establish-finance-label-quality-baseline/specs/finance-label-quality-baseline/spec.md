## ADDED Requirements

### Requirement: Frozen local evaluation inputs
系统 SHALL 以固定本地转录、来源清单、标注、预测及版本哈希作为评测输入。离线评分 SHALL 不访问业务数据库或外部模型；显式模型运行 SHALL 保留完整输入范围、原始输出和实际提示词/模型/处理模式。系统 SHALL 不按数据库内容替换冻结输入或静默截断。

系统 SHALL 在运行前保存全文或分块对应的完整逻辑单元计划，并保存每次尝试结果。每单元最多 3 次评测层尝试；首次失败记录 SHALL 保留，重试耗尽后仍未成功 SHALL 计为处理不完整。

#### Scenario: Repeat scoring without a database
- **WHEN** 使用同一组冻结输入、预测及人工裁决在无数据库环境重新评分
- **THEN** 各指标及计数与此前一致，且不触发数据库或模型连接

#### Scenario: Incomplete model processing
- **WHEN** 生成预测时部分调用失败或输入超限而未全部处理
- **THEN** 系统记录不完整范围及失败状态，保留原请求素材，不将失败视为成功无观点

### Requirement: Explicit annotation qualification
系统 SHALL 区分 demo、draft 和 human_verified，并记录实际复核人、时间、原音视频核验状态及标注完整性。人工观点 SHALL 具有稳定 ID、声明、字段标签及可定位的原文证据。模型生成结果 SHALL 不自动获得 human_verified 资格。

#### Scenario: Existing demonstration annotations
- **WHEN** 使用当前两条待复核样例运行评测
- **THEN** 报告只能表示演示或待审状态，不得输出正式质量通过

#### Scenario: Evidence cannot be resolved
- **WHEN** 人工基准中的证据段不存在、摘录不符或缺少来源定位
- **THEN** 系统拒绝将其作为有效正式基准并列出具体缺失项

### Requirement: Finance annotation meaning and uncertainty
标注规范 SHALL 明确对象、方向、期限、条件、讲话归属、证据的定义和正反例，区分未提及、无法判断和不适用。系统 SHALL 将忠实于转录、已核验原音视频、已核验外部事实分开表达；尚无模型输出的标签 SHALL 标明未评估。

#### Scenario: Conditional or attributed statement
- **WHEN** 原文是带条件、期限或转述对象的观点
- **THEN** 人工标注与语义裁决保留这些限定，丢失关键限定的预测不得被判为原文完整支持

### Requirement: One-to-one human semantic adjudication
系统 SHALL 允许人工确认预测与金标准的一对一对齐，并记录 pending/pass/fail 的原文支持裁决。匹配 SHALL 不以方向、期限或其他待测字段相同为前提。文字相似度、模型 confidence 和证据 ID 存在 SHALL 不被当作语义正确的判据。

#### Scenario: Opposite stance on the same statement
- **WHEN** 已对齐的预测方向与人工标注相反
- **THEN** 该观点仍进入方向指标分母并计为错误，同时记录语义裁决失败

#### Scenario: Duplicate prediction
- **WHEN** 两条预测表达同一条金标准观点
- **THEN** 该金标准最多获得一次正确覆盖，额外重复预测保留在精确率分母且不得重复得分

#### Scenario: Pending semantic review
- **WHEN** 仍有预测未获得人工语义裁决
- **THEN** 报告列出待审数及覆盖率，只给已审范围的阶段性统计，不输出完整正式语义精确率或质量通过

### Requirement: Honest metric denominators
系统 SHALL 输出分子、分母、适用范围和按来源类型的统计。精确率分母 SHALL 包含全部预测；证据指标 SHALL 包含无证据项；召回率 SHALL 包含完整标注素材中所有应抽观点及失败素材的漏抽。字段指标 SHALL 在已对齐且该字段适用的观点内计算，预测漏填计错。结构通过率 SHALL 为首次尝试完整通过 schema 的逻辑单元数除以预先计划的全部逻辑单元数，首次失败或漏调用计不通过。零分母 SHALL 显示不适用，不得转成 100%。

#### Scenario: Retry recovers a malformed response
- **WHEN** 某逻辑单元首次结构失败但在有限重试内成功
- **THEN** 其最终输出可以参与语义裁决，首次结构指标仍计一次失败，且不得把重试次数计入逻辑单元分母

#### Scenario: Missing evidence prediction
- **WHEN** 一条预测没有证据或引用越界
- **THEN** 该预测计入证据缺失分子及全部预测分母，不能从指标计算中跳过

#### Scenario: Failed case with expected viewpoints
- **WHEN** 一条完整标注素材抽取失败
- **THEN** 该素材保留在运行总数，其全部未覆盖金标准计为漏抽

#### Scenario: Zero expected and predicted viewpoints
- **WHEN** 完整人工确认该素材无观点且预测为空
- **THEN** 将其记作成功无观点，精确率/召回率的零分母显示不适用，不凭此宣称质量达标

### Requirement: Quality acceptance distinct from tool completion
系统 SHALL 区分 not_evaluated、failed 和 passed。正式验收 SHALL 要求独立验收组至少包含 20 个真实素材、300 条人工观点、5 位人物和 10 个主题，且直播、短视频、长视频均有覆盖；开发与验收组 SHALL 按共同来源隔离。所有必要人工裁决与完整标注 SHALL 完成。任何演示、必需指标缺失、处理不完整或覆盖不足 SHALL 不得 passed。

正式指标 SHALL 满足：证据缺失率为 0、首次尝试结构通过率至少 99%、明确方向准确率至少 90%、主题与实体准确率各至少 95%、观点召回率至少 85%、语义精确率至少 98%。阈值 SHALL 在无 baseline 时同样启用。有限重试已恢复的首次结构失败 SHALL 通过 99% 指标判断，不单独强制失败；重试后仍未恢复 SHALL 因处理不完整而失败。以上为本提案待确认的首轮门槛，不是当前准确率声明。

系统 SHALL 按优先级判定主状态：非法输入/评测器整体错误（not_evaluated，退出码 3）；最终处理不完整（failed，1）；完整演示（not_evaluated，0）；正式材料资格/人审不足（not_evaluated，2）；完整正式运行未达门槛（failed，1）；完整正式通过（passed，0）。全部阻碍原因 SHALL 同时列出。

#### Scenario: Incomplete human review and exhausted model retries
- **WHEN** 同一批材料既缺人工复核又有重试耗尽的失败逻辑单元
- **THEN** 主状态为 failed、退出码 1，报告同时列出处理失败与人审缺口

#### Scenario: Too few verified cases
- **WHEN** 指标看似达标但正式人审素材数量或覆盖不足
- **THEN** 系统输出 not_evaluated、实际覆盖与缺口，并返回非零验收结果

#### Scenario: Missing baseline
- **WHEN** 正式评测未提供历史 baseline
- **THEN** 系统仍按全部资格与指标门槛判断，不跳过验收

#### Scenario: Completed valid acceptance run
- **WHEN** 正式基准资格、处理完整性、人工裁决、必要指标和全部门槛均满足
- **THEN** 系统仅声明本次数据集和运行范围的质量验收 passed，保留分组结果与样本量，不外推为线上总体准确性

### Requirement: Versioned reports and stale review detection
系统 SHALL 输出 JSON 与 Markdown 报告，包含 mode、quality_status、数据/模型/提示词/处理模式/评分规则版本、阈值、计数、逐条差异和待审项。人工裁决 SHALL 绑定输入、标注和预测内容哈希；修改关联内容后 SHALL 将旧裁决标为失效。不同数据或评分口径的 baseline SHALL 不直接比较优劣。报告 SHALL 不含凭据。

#### Scenario: Prediction corrected after review
- **WHEN** 已审核的预测、转录或人工标注内容发生变化
- **THEN** 旧裁决失效并提示重新审核，不能沿用旧 pass

#### Scenario: Legacy report comparison
- **WHEN** baseline 缺版本或使用不同输入/评分口径
- **THEN** 系统说明不可直接比较的原因，不输出误导性的提升或退化结论
