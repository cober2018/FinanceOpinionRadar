#!/usr/bin/env bash
# radar 看门狗（launchd 常驻，com.financeopinionradar.watchdog，Plan #7）。
#
# 每 RADAR_WATCHDOG_INTERVAL 秒检查组件（Postgres/Redis/API :8010/worker-beat），
# 仅当组件「上一轮还在、这一轮没了」才执行 dev_up.sh up（幂等，已在跑的跳过）。
# 首轮只记录基线不拉起——冷启动交给「开机自启」开关，二者语义分开：
#   看门狗 = 救活运行中掉线的组件；开机自启 = 登录后的首次拉起。
# 注意：看门狗开启期间，手动停止的组件会在一个周期内被自动拉起，维护前先在
# 设置页关闭看门狗。
set -u
cd "$(dirname "$0")/.."
INTERVAL=${RADAR_WATCHDOG_INTERVAL:-60}

# REDIS_PORT 从 .env 读（本机多项目共用 6379，本项目容器走独立端口）
REDIS_PORT=${REDIS_PORT:-$(sed -n 's/^REDIS_PORT=//p' .env 2>/dev/null)}
REDIS_PORT=${REDIS_PORT:-6379}
mkdir -p logs

log() { echo "$(date '+%F %T') [radar-watchdog] $*"; }

pg_up() { docker exec financeopinionradar-postgres-1 pg_isready -U radar -d radar >/dev/null 2>&1; }
redis_up() { redis-cli -h 127.0.0.1 -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; }
api_up() { lsof -nP -iTCP:8010 -sTCP:LISTEN >/dev/null 2>&1; }
worker_up() { pgrep -f "celery -A app.worker.celery_app" >/dev/null 2>&1; }

current_state() {
  printf 'pg:%d;redis:%d;api:%d;worker:%d' \
    "$(pg_up && echo 1 || echo 0)" \
    "$(redis_up && echo 1 || echo 0)" \
    "$(api_up && echo 1 || echo 0)" \
    "$(worker_up && echo 1 || echo 0)"
}

was_up() { # $1=状态串 $2=组件名 → 0/1
  printf '%s' "$1" | tr ';' '\n' | grep "^$2:" | cut -d: -f2
}

log "watchdog start (interval=${INTERVAL}s)"
prev=$(current_state)
log "baseline: $prev（首轮只记录不拉起）"
while true; do
  sleep "$INTERVAL"
  cur=$(current_state)
  dropped=""
  for comp in pg redis api worker; do
    if [ "$(was_up "$prev" "$comp")" = "1" ] && [ "$(was_up "$cur" "$comp")" = "0" ]; then
      dropped="$dropped $comp"
    fi
  done
  if [ -n "$dropped" ]; then
    log "检测到掉线:$dropped → dev_up.sh up"
    if ! scripts/dev_up.sh up >> logs/watchdog-devup.log 2>&1; then
      log "dev_up.sh up 退出非零，下一周期重试"
    fi
    cur=$(current_state)
  fi
  prev=$cur
done
