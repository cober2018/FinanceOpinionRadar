#!/usr/bin/env bash
# RAD-104：PostgreSQL 每日备份（pg_dump 自定义格式，保留最近 N 份由调用方清理）。
# 用法：scripts/backup_db.sh [output_dir]（默认 ~/backups/radar）
set -euo pipefail
OUT="${1:-$HOME/backups/radar}"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"
docker exec financeopinionradar-postgres-1 pg_dump -U radar -Fc radar > "$OUT/radar_$STAMP.dump"
echo "备份完成: $OUT/radar_$STAMP.dump ($(du -h "$OUT/radar_$STAMP.dump" | cut -f1))"
echo "恢复演练: see docs/runbook/db-migration-failed.md"
