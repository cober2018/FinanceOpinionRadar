# 迁移失败

**现象**：`make migrate` 报错中断。

**诊断**：`.venv/bin/python -m alembic -c apps/api/alembic.ini current` 看停留版本；`alembic history` 看链。

**恢复**：
1. 修复 SQL 层问题（锁/权限/磁盘）
2. `alembic upgrade head` 重放；部分应用的事务迁移自动回滚（transactional DDL）
3. 最坏情况恢复备份：`docker exec -i financeopinionradar-postgres-1 pg_restore -U radar -d radar --clean < backup.dump`

**会重复数据吗**：恢复备份会回滚到备份时点。
**需要重跑吗**：备份之后的抽取/转录任务需要重跑。
