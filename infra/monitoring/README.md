# 运行监控（2026-11-18 优化项 4）

## 现状：零基建方案

`/metrics`（API :8010，Prometheus 文本格式）+ `scripts/check_metrics.sh`（在生产机
cron/launchd 周期跑，差值告警，退出码非 0 = 有告警）。不引入 Prometheus 服务端，
后续要接时指标已在，无需改代码。

## 指标清单

| 指标 | 类型 | 含义 |
|---|---|---|
| `radar_http_requests_total{route,status}` | counter | API 请求计数（进程内，重启归零） |
| `radar_http_request_seconds_total{route}` | counter | API 请求耗时累计（秒） |
| `radar_celery_tasks_total{name,state}` | counter | 任务结果计数（Redis 聚合，跨 worker 进程） |
| `radar_celery_task_ms_total{name}` | counter | 任务耗时累计（毫秒） |
| `radar_identity_pool_exhaustion_1h` | gauge | 抖音身份池熔断事件（1h 窗口） |
| `radar_queue_depth{queue}` | gauge | 四条 celery 队列深度 |
| `radar_db_pool_checkedout` | gauge | API 进程 DB 连接占用 |
| `radar_job_run_1h{status}` | gauge | 近 1h 任务流水结果分布 |

采集端纪律：Redis/DB 不可达时对应指标缺失而非报错——`/metrics` 本身永远 200。

## 五条告警（check_metrics.sh 内置阈值）

| # | 条件 | 阈值依据 | 处置方向 |
|---|---|---|---|
| R1 | `/metrics` 不可达 | API 存活性 | 看 `~/radar-api.log` / 重启 |
| R2 | HTTP 5xx 增量 > 5 | 正常期 0 | 看 API 日志尾部 |
| R3 | 身份池熔断(1h) > 5 | 正常 0；2026-11-10 事故时数十 | 补身份 cookie / 降并发 |
| R4 | 队列深度 > 100 | 正常 < 10 | worker 停了 / 长任务堵塞 |
| R5 | 近 1h 失败任务 > 50 | 正常 < 10（YouTube 退避期除外） | 看 worker 日志按 error 分组 |
| R6 | 引擎容器 / 双 worker 存活 | — | docker restart / 查进程 |

差值类（R2）首次运行只记基线。状态文件 `/tmp/radar-metrics-state`。

## 接入方式（生产，用户自行决定频率）

```bash
# 手动一次
scripts/check_metrics.sh
# cron 示例：每 10 分钟，输出追加到日志
*/10 * * * * cd ~/Documents/project/FinanceOpinionRadar && scripts/check_metrics.sh >> logs/metrics-check.log 2>&1
```

## 将来接 Prometheus 时的等价 PromQL

```promql
# R2
sum(increase(radar_http_requests_total{status=~"5.."}[1h])) > 5
# R3
radar_identity_pool_exhaustion_1h > 5
# R4
max(radar_queue_depth) > 100
# R5
sum(increase(radar_celery_tasks_total{state="FAILURE"}[1h])) > 50
# R6 用 blackbox/up 生态，或保留脚本检查
```
