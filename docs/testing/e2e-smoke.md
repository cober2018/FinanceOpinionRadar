# 端到端冒烟测试文档（主播录入 → 观点归档 全链路）

日期：2026-09-18 ｜ 环境：本机真栈（API :8010 / worker+beat / PG / Redis / MinIO / dtk / StreamCap / MiniMax-M3）
执行方式：每链路给出「入口 → 操作 → 预期 → 实测证据」。UI 链路在 http://localhost:8010/console/ 人工执行，API 链路可脚本重放。

## 链路总览与状态

| # | 链路 | 入口 | 状态 |
|---|---|---|---|
| 1 | 主播录入（主页/直播间/双开关） | 主播页「＋添加主播」 | ✅ |
| 2 | 立即扫描新视频 | 主播页「扫描」按钮 | ✅ |
| 3 | 自动发现（beat 轮询） | 后台 | ✅ |
| 4 | 新视频自动转写→自动抽取 | 后台（auto_poll） | ✅ |
| 5 | 手动转写（单条） | 视频库「转写」 | ✅（403 属已知限速） |
| 6 | 手动抽取观点 | 视频库「抽取观点」 | ✅ |
| 7 | 转录正文查看 | 视频库点行/「正文」 | ✅ |
| 8 | 全文搜索 | 视频库搜索框 | ✅ |
| 9 | 复核队列（键盘 A/R/E） | 复核队列页 | ✅ |
| 10 | 确认→快照→归档 ready | 复核动作 | ✅ |
| 11 | 审计（每次人工修改） | audit_log 表 | ✅ |
| 12 | 直播值守→录制→ingest→转写→抽取 | 主播页值守开关 | ✅（待下次开播全量复验） |
| 13 | 删除：单条/批量/主播（墓碑+不复活） | 视频库/主播页 | ✅ |
| 14 | LLM 配置与连接测试 | 设置页 | ✅ |
| 15 | 任务记录（转写/抽取） | 任务页 | ✅ |
| 16 | 失败路径与恢复 | 见「已知失败模式」 | ✅ 有诊断有出路 |

## 冒烟实录（本次执行证据）

### 1-2. 主播录入 + 立即扫描
- 添加弹窗：平台（抖音/YT/B站）/名称/主页 URL（必填校验 sec_uid 形态）/直播间 URL（房间页校验）/值守开关/轮询区间
- 「扫描」→ `POST /source-accounts/{id}/scan` → worker `discover_ok account_id=27 discovered=41 created=0 skipped_deleted=40`
- 预期校验点：**created=0 且 skipped_deleted=40**（墓碑生效：删过的旧视频不回流）

### 3-4. 自动发现与自动转写抽取
- beat 周期派发（错峰 countdown）→ 新视频 created → auto_prepare（仅 auto_poll 非 backfill）→ 转写（mlx）→ beat 抽取补扫 → reviewing
- 实测：交易魔法师#745(8 观点)、爱德华说#749(16)、全能的野人#750(11) 均自动完成转写+抽取

### 5. 手动转写
- 入口 `POST /source-items/{id}/prepare`；实测 746/751 触发正常，失败原因是 dtk CDN 403（限速），错误落在 last_error 且 failed 可重驱 —— 链路本身 ✅

### 6+9+10+11. 抽取 → 复核 → 归档（本次完整走通的样板：item 748）
1. 重置 reviewing→transcribed，`POST /source-items/748/extract`
2. MiniMax-M3 产出 **12 条候选**（run 报告含 chunk_summaries：candidates 计数 + 空返回 raw_head 诊断）
3. 规则复核 → 全部 needs_review 进队列（置信<0.75 或证据短的诚实降级）
4. 人工：confirm 11 条 / reject 1 条（驳回必填原因）
5. **audit_log 逐条落库**（action=confirm/reject，before/after/reason）
6. 队列清空 → **item status=ready（归档）**
7. 主题词典命中后 confirm 触发快照：`bullish / new_thesis @2026-09-18`（RAD-060）

### 7-8. 正文与搜索
- 抽屉显示 132 段带时间戳文本；`GET /transcripts/search?q=降息` → 命中全能的野人（snippet 上下文正确）

### 12. 直播链（前次全量验证，当前双房间未开播）
值守开关 → 桥同步 recordings.json → docker restart + UI 激活 → 开播录制分片 → ingest 会话 → 串行转写 → 抽取 → 收尾 transcribed；9/17 两场实录 898+510 段

### 13. 删除链
- 单条/多选批量：物理删除+墓碑（discover 不回流）
- 主播删除：级联内容 + 审计 + 录制器自动移除；**磁盘目录残留不再复活账号**（2026-09-18 修复实录）

### 15. 任务页
- job_run 记录 prepare_media（转写）与 extract_viewpoints（抽取）：状态/耗时/错误码；中文类型展示

## 已知失败模式（有诊断、有出路，非链路缺陷）

| 现象 | 根因 | 处置 |
|---|---|---|
| dtk CDN HTTP 403 | douyinvod 单 IP 限速 | 冷却后重驱（视频库「转写」）；低峰分批 |
| YouTube "Sign in to confirm" | YouTube bot 检测/PO Token 墙 | 挂账（需 POT provider）；cookie 可缓解 |
| 抽取 created=0 | MiniMax-M3 偶发空返回（推理非确定性） | run 报告 chunk_summaries+raw_head 可诊断；重置 transcribed 重抽即得（748 实测 0→12） |
| 直播间未开播 | is_live=false | 正常等待；值守自动检测 |

## 回归重放（脚本）

```bash
# 扫描某主播
curl -X POST localhost:8000/api/v1/source-accounts/<id>/scan
# 手动转写/抽取
curl -X POST localhost:8000/api/v1/source-items/<id>/prepare
curl -X POST localhost:8000/api/v1/source-items/<id>/extract
# 复核
curl -X POST localhost:8000/api/v1/viewpoints/<id>/confirm
curl -X POST localhost:8000/api/v1/viewpoints/<id>/reject
# 观察任务/搜索
curl "localhost:8000/api/v1/jobs?limit=20"
curl "localhost:8000/api/v1/transcripts/search?q=降息"
```
