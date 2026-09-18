# Redis 不可用

**现象**：API `/health` ok 但任务不派发；worker 日志 redis 连接错误。

**诊断**：`docker compose ps redis`；`redis-cli ping`。

**恢复**：`docker compose up -d redis`；队列数据丢失可接受（beat 会重新派发周期任务；手动投递的重驱）。

**会重复数据吗**：不会。
**需要重跑吗**：Redis 里的排队任务丢失，按需重驱。
