## Why

后端已提供 `/open/v1/broadcasters`，但开放接口目录固定只有视频总结，导致使用者无法发现或试读主播清单。用户已确认补一个独立主播清单服务。

## What Changes

- 开放接口新增可选择的“主播清单”服务。
- 展示 GET 路径、API Key 鉴权、完整快照语义、五个返回字段和真实在线试读。
- 主播清单仅展示拉取，保留视频总结原有拉取/推送详情与管理功能。

## Capabilities

### New Capabilities

- `broadcaster-open-interface-catalog`: 主播清单的目录、只读详情与试读。

### Modified Capabilities

无；视频总结服务的既有语义保持不变。

## Impact

只改已有 React 开放接口页面与相关说明，不修改后端、数据库或推送配置。部署更新不在此次代码修补范围内。
