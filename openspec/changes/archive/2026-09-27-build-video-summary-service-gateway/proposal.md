## Why

“开放接口”目前只把拉取 API 和推送事件写成两块说明文字，用户无法像参考项目的数据服务网关一样从服务目录选择条目、查看参数与输出契约，并在同一处完成接入操作。视频审核后总结是同一项数据服务，可通过拉取或推送两种方式交付。

## What Changes

- 将“开放接口”改为网关式服务目录：左侧只有一个“视频审核后总结”服务，右侧以“拉取 / 推送”切换交付方式，分别展示对应的协议、字段和操作；不重复登记同一项数据服务。
- “拉取”视图以现有 `GET /open/v1/video-summaries` 为主，兼容单视频查询作为详情补充；在该视图内管理 API Key 的启用/停用与吊销，并可用临时输入的 Key 发起真实只读试读，显示实际响应或错误。
- “推送”视图展示现有视频总结 Webhook 事件契约，在该视图内管理通用 HTTPS Webhook 的订阅、渠道启停及最近投递/对账；清楚区分“渠道连通测试”与“真实视频总结事件投递”。
- 顶部“拉取 / 推送”切换只负责显示详情，不直接启停服务；操作必须在各自详情中明确执行。拉取侧补一个经审计的 API Key 暂停/恢复管理动作，保留永久吊销；推送侧复用现有渠道订阅/启停。不引入接口定义编辑/发布、动态注册表、审批或伪造的监控指标；对外读取协议和数据库结构不变。

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `video-summary-open-interface-catalog`: 从文字目录提升为单服务、双交付方式的网关详情，补充参数/字段详情、真实拉取试读和推送订阅操作入口。

## Impact

主要涉及 `apps/web/src/App.tsx`、`apps/web/src/App.css`、API Key 管理路由、前后端与根 README；复用现有开放拉取路由、Webhook 渠道和投递接口。参考 `/Users/mshengran/Project/DreamOAgents/frontend/src/views/DataManagement/APIs/index.vue` 的目录与详情交互，不修改该参考项目。
