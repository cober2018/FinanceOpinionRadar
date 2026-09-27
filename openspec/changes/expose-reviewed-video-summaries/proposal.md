## Why

内部已能在视频观点复核后生成一条视频总结，开放接口也能按视频 ID 查询它；但外部系统无法连续增量拉取这些总结，现有推送发送的是逐条观点而非视频总结。第一版需要让拉取和推送同时交付同一份可核对的结果，避免两端口径不一致。

## What Changes

- 将“每视频一条审核后总结”定义为对外交付单元：主播名称、视频时间、总结结论，并带稳定的视频标识、总结版本与更新时间。保留现有单视频查询路径的兼容性。
- 在 `/open/v1` 增加经 API Key 鉴权的分页、增量拉取入口；只返回已有确认观点、没有待审观点且总结对应当前已确认观点版本的视频。无总结、时间缺失或状态不满足时明确处理，不伪造内容或时间。
- 在“开放接口”模块增加视频总结 Webhook 订阅与交付记录。总结首次就绪或有效版本更新后，按同一载荷推送；失败可重试，未知结果须对账，重复事件可识别，不影响既有逐条观点推送。
- 展示拉取契约、订阅状态和投递结果，保留接口调用审计；不把一次 HTTP 成功写成下游已消费或已发布。

## Capabilities

### New Capabilities

- `reviewed-video-summary-feed`: 每视频一条审核后总结的统一载荷、资格门禁、按视频查询兼容、分页与增量拉取。
- `reviewed-video-summary-push`: 同一总结版本的 Webhook 订阅、可靠投递、重试与未知结果对账。
- `reviewed-video-summary-open-console`: 开放接口控制台内的契约说明、订阅配置和投递可见性。

### Modified Capabilities

None. Existing top-level specs do not define the `/open/v1` video-summary contract.

## Impact

主要涉及 `apps/api/app/api/open/`、`apps/api/app/api/v1/open_admin.py`、`apps/api/app/services/summarizer.py`、推送/持久化相关服务与迁移，以及 `apps/web/src/App.tsx` 的开放接口模块。复用现有 `X-API-Key`、调用审计和观点复核链路；不改变内容中心、外部平台发布或已存在的逐条观点接口行为。
