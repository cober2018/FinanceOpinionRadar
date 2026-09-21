#!/usr/bin/env bash
# StreamCap v1.0.3 无头会话保活。
#
# 背景：StreamCap web 模式下，60s 开播探测任务只在【第一个浏览器会话连上时】启动
# （main.py load_app 里 global_state.periodic_tasks_started 门闩）。会话断开后
# periodic_check 引用已死的 page 崩溃退出，门闩仍为 True，之后即使浏览器重连也
# 不会重启任务——表现为 docker logs 再无任何 "Live URL" 探测行，录制与 is_live
# 全部失效，必须"重启容器 + 新会话立即接入"才能复活。
#
# 本脚本在宿主机维持一个无头 Chrome 会话指向 StreamCap UI，每轮循环：
#   清掉旧保活 Chrome → 重启 streamcap → 启动 headless Chrome → 监控两者；
#   Chrome 退出 / streamcap 被外部重启（recorder_bridge 改配置会 restart）/
#   容器消失 / docker 未就绪 → 回到循环头重来。
#
# 常驻方式：make streamcap-keeper（launchd LaunchAgent，开机自动拉起；
# 日志 logs/keepalive.log）。单例锁防止与残留的旧实例互相杀对方的 Chrome。
set -u

CONTAINER="${STREAMCAP_CONTAINER:-streamcap}"
URL="${STREAMCAP_URL:-http://localhost:5001}"
CHROME="${CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PROFILE_DIR="${STREAMCAP_PROFILE:-${TMPDIR:-/tmp}/streamcap-keepalive-profile}"
POLL_SEC="${STREAMCAP_KEEPALIVE_POLL:-15}"
LOCK_DIR="${TMPDIR:-/tmp}/streamcap-keepalive.lock"

log() { echo "$(date '+%F %T') [streamcap-keepalive] $*"; }
started_at() { docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null || echo gone; }
container_ok() { docker inspect "$CONTAINER" >/dev/null 2>&1; }

# --- 单例锁：已有存活实例则退出（锁目录残留时按 pid 死活接管） ---
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  old_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    log "another keeper alive (pid $old_pid), exit"
    exit 0
  fi
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
fi
echo $$ > "$LOCK_DIR/pid"
chrome_pid=""
cleanup() {
  [ -n "$chrome_pid" ] && kill "$chrome_pid" 2>/dev/null
  rm -rf "$LOCK_DIR"
}
trap cleanup EXIT INT TERM

# --- 配置目录自愈：缺 language.json 时会话初始化必崩（SettingsPage.load_language
# 空 dict IndexError → 定时任务不会启动）。目录可能是易失路径，每轮循环前检查。 ---
CONFIG_DIR="${STREAMCAP_CONFIG_DIR:-}"
detect_config_dir() {
  docker inspect "$CONTAINER" --format \
    '{{range .Mounts}}{{if eq .Destination "/app/config"}}{{.Source}}{{end}}{{end}}' 2>/dev/null
}
if [ -z "$CONFIG_DIR" ]; then
  CONFIG_DIR=$(detect_config_dir)
fi
ensure_language_json() {
  # docker 在 launchd 环境可能晚就绪，检测失败时每轮重试
  if [ -z "$CONFIG_DIR" ]; then
    CONFIG_DIR=$(detect_config_dir)
  fi
  [ -n "$CONFIG_DIR" ] || return 0
  if [ ! -f "$CONFIG_DIR/language.json" ]; then
    printf '{\n  "Chinese": "zh_CN",\n  "English": "en"\n}\n' > "$CONFIG_DIR/language.json" \
      && log "restored missing $CONFIG_DIR/language.json"
  fi
}

log "start (container=$CONTAINER url=$URL chrome=$CHROME config=$CONFIG_DIR)"
while true; do
  ensure_language_json
  # 先清掉所有用保活 profile 的旧 Chrome：其一，profile 的 ProcessSingleton 锁
  # 会让新 Chrome 把 URL 转交给旧实例后立刻退出；其二，旧会话若抢在新会话前
  # 重连，新会话只会看到 "Periodic tasks already running" 而任务其实已随旧会话死亡。
  pkill -f "user-data-dir=$PROFILE_DIR" 2>/dev/null
  sleep 2
  until container_ok; do
    log "docker/container unavailable, wait 15s"
    sleep 15
  done
  log "restarting $CONTAINER"
  docker restart "$CONTAINER" >/dev/null
  sleep 8
  base=$(started_at)
  if [ "$base" = "gone" ]; then
    log "container gone after restart, retry"
    sleep 5
    continue
  fi
  "$CHROME" \
    --headless=new --disable-gpu --no-first-run --no-default-browser-check \
    --user-data-dir="$PROFILE_DIR" --window-size=1280,900 \
    "$URL" >/dev/null 2>&1 &
  chrome_pid=$!
  log "chrome pid=$chrome_pid container_started_at=$base"
  while kill -0 "$chrome_pid" 2>/dev/null; do
    sleep "$POLL_SEC"
    cur=$(started_at)
    if [ "$cur" = "gone" ]; then
      log "container disappeared, cycling"
      break
    fi
    if [ "$cur" != "$base" ]; then
      log "container restarted externally (recorder_bridge config sync?), cycling chrome"
      break
    fi
  done
  kill "$chrome_pid" 2>/dev/null
  wait "$chrome_pid" 2>/dev/null
  chrome_pid=""
  sleep 3
done
