#!/usr/bin/env bash
# 开发环境一键启动/诊断（幂等：已在跑的组件跳过，机器重启或进程死后重复执行安全）。
#
#   scripts/dev_up.sh up      拉齐全部组件：docker 依赖(PG/Redis/MinIO) + dtk 容器组
#                             + API :8010 + worker-beat，并汇总状态
#   scripts/dev_up.sh status  只诊断不启动：五组件 OK/DOWN + 修复提示
#
# 设计约束（与用户对齐）：开发环境不做开机自启——本脚本只在用户显式执行时工作；
# 进程级保活不引入 launchd，死后由用户跑 make up 或看前端红色横幅提示后恢复。
set -uo pipefail
cd "$(dirname "$0")/.."

# REDIS_PORT 从 .env 读（本机多项目共用 6379，本项目容器走独立端口）
REDIS_PORT=${REDIS_PORT:-$(sed -n 's/^REDIS_PORT=//p' .env 2>/dev/null)}
REDIS_PORT=${REDIS_PORT:-6379}

API_PORT=8010
API_LOG=/tmp/radar-api-8010.log
WORKER_LOG=/tmp/radar-worker-beat.log
PY=.venv/bin/python
DTK_CONTAINERS=(douyin-api dtk-worker dtk-postgres dtk-redis)

c_ok=$'\033[32mOK\033[0m'
c_down=$'\033[31mDOWN\033[0m'

api_alive() { lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN >/dev/null 2>&1; }
worker_alive() { pgrep -f "celery -A app.worker.celery_app" >/dev/null 2>&1; }
pg_alive() { docker exec financeopinionradar-postgres-1 pg_isready -U radar -d radar >/dev/null 2>&1; }
# 本项目 Redis 为 compose 容器（端口见 .env REDIS_PORT，避开共用 6379）
redis_alive() { redis-cli -h 127.0.0.1 -p "$REDIS_PORT" ping 2>/dev/null | grep -q PONG; }
dtk_alive() { curl -s -o /dev/null -m 3 "http://localhost:8080/health" 2>/dev/null; }

wait_pg() {
  for _ in $(seq 1 30); do
    pg_alive && return 0
    sleep 1
  done
  return 1
}

start_deps() {
  echo "==> docker compose up -d（postgres/redis/minio）"
  docker compose up -d || true
  # dtk 容器组属外部 compose：存在才 start，已在跑无害，未安装忽略
  echo "==> dtk 容器组（douyin-api/dtk-worker/dtk-postgres/dtk-redis）"
  docker start "${DTK_CONTAINERS[@]}" >/dev/null 2>&1 || true
}

start_api() {
  echo "==> 启动 API :${API_PORT}（日志 ${API_LOG}）"
  nohup "$PY" -m uvicorn app.main:app --reload --port "$API_PORT" --app-dir apps/api \
    >"$API_LOG" 2>&1 &
  disown
  for _ in $(seq 1 20); do
    api_alive && { echo "    API 就绪"; return 0; }
    sleep 1
  done
  echo "    API 未就绪，查看 $API_LOG" >&2
  return 1
}

start_worker() {
  echo "==> 启动 worker+beat（全队列 default/media/llm/danmaku，日志 ${WORKER_LOG}）"
  nohup "$PY" -m celery -A app.worker.celery_app worker --beat \
    -Q default,media,llm,danmaku --loglevel=info >"$WORKER_LOG" 2>&1 &
  disown
  for _ in $(seq 1 20); do
    worker_alive && { echo "    worker 就绪"; return 0; }
    sleep 1
  done
  echo "    worker 未就绪，查看 $WORKER_LOG" >&2
  return 1
}

report() {
  pg_alive && p=$c_ok || p=$c_down
  redis_alive && r=$c_ok || r=$c_down
  dtk_alive && d=$c_ok || d=$c_down
  api_alive && a=$c_ok || a=$c_down
  worker_alive && w=$c_ok || w=$c_down
  echo
  echo "组件状态："
  echo "  Postgres(容器)   $p"
  echo "  Redis(:$REDIS_PORT)        $r"
  echo "  dtk 解析(:8080)  $d   （不监测抖音可忽略）"
  echo "  API(:$API_PORT)        $a"
  echo "  worker+beat      $w"
  if [[ $a == *DOWN* || $w == *DOWN* || $p == *DOWN* ]]; then
    echo
    echo "有组件 DOWN：运行 make up 一键拉起（幂等，可重复执行）。"
  fi
}

cmd_up() {
  start_deps
  wait_pg || { echo "Postgres 30s 未就绪" >&2; exit 1; }
  if api_alive; then echo "==> API :$API_PORT 已在跑，跳过"; else start_api; fi
  if worker_alive; then echo "==> worker 已在跑，跳过"; else start_worker; fi
  report
}

cmd_status() { report; }

case "${1:-up}" in
  up) cmd_up ;;
  status) cmd_status ;;
  *) echo "用法: $0 [up|status]" >&2; exit 2 ;;
esac
