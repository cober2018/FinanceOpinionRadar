# Worker 卡死 / 任务积压

**现象**：beat 周期日志正常但任务不消化；`/jobs` 里 running 长时间不结束。

**诊断**：
- `ps aux | grep celery` 确认进程在
- `.venv/bin/python -c "from app.worker.celery_app import celery_app; import redis; from app.core.settings import get_settings; print(redis.Redis.from_url(get_settings().redis_url).llen('celery'))"` 看队列深度
- `docker exec financeopinionradar-postgres-1 psql -U radar -d radar -c "SELECT pg_stat_activity"` 看卡住的查询

**恢复**：重启 worker（`make worker-beat`）；长事务锁表时先 `SELECT pg_terminate_backend(pid)`。

**会重复数据吗**：不会——prepare/extract 均幂等（行锁/状态门槛/advisory lock）。
**需要重跑吗**：in-flight 任务重启后不自动重投，按 README 分批重驱。
