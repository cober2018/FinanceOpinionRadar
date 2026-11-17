#!/usr/bin/env bash
# 雷达运行指标告警检查（2026-11-18 优化项 4 的告警面，零额外基建）。
#
# 用法：scripts/check_metrics.sh [API_BASE]        # 默认 http://localhost:8010
# 在生产机上由 cron/launchd 周期执行；输出一行一结论，发现任一问题退出码 1。
# 增量类告警（5xx、任务失败）靠 /tmp 状态文件做两次执行之间的差值；
# 首次执行只记基线不告警。阈值依据 2026-11 实测水位，宽到只抓真事故。
#
# 五条规则（与 infra/monitoring/README.md 对应）：
#   R1 /metrics 可达            R2 HTTP 5xx 增量 > 5
#   R3 身份池熔断(1h) > 5        R4 任一队列深度 > 100
#   R5 近 1h 失败任务 > 50       R6 引擎容器/双 worker 存活（docker/pgrep 可用时）
set -uo pipefail

BASE="${1:-http://localhost:8010}"
STATE=/tmp/radar-metrics-state
WARN=0

raw=$(curl -sf -m 8 "$BASE/metrics") || { echo "✗ R1 /metrics 不可达（API 挂了或未部署本功能）"; exit 1; }
echo "✓ R1 /metrics 可达"

# metric_total{labels} value → 取 value（标签含逗号，用首个空格切分）
get() { awk -v m="$1" 'index($1, m) == 1 {print $2; exit}' <<<"$raw"; }
get_all() { awk -v m="$1" 'index($1, m) == 1 {print $2}' <<<"$raw"; }

# 增量读取：状态文件里的旧值
prev() { sed -n "s/^$1=//p" "$STATE" 2>/dev/null | head -1; }
save() { printf "%s=%s\n" "$1" "$2"; }  # 仅写入状态文件，不打印（见文件尾重定向）

new_5xx=$(get_all 'radar_http_requests_total{route="[^[}]*",status="5' | paste -sd+ - | bc 2>/dev/null)
new_5xx=$(awk -v s="${new_5xx:-0}" 'BEGIN{print (s+0)}')
old_5xx=$(prev http_5xx)
if [ -n "$old_5xx" ]; then
  d=$((new_5xx - old_5xx)); [ "$d" -gt 5 ] && { echo "✗ R2 HTTP 5xx 增量 $d > 5"; WARN=1; } || echo "✓ R2 HTTP 5xx 增量 $d"
fi

exh=$(get radar_identity_pool_exhaustion_1h)
[ "${exh:-0}" -gt 5 ] 2>/dev/null && { echo "✗ R3 身份池熔断(1h)=$exh > 5（补身份 cookie 或降并发）"; WARN=1; } \
  || echo "✓ R3 身份池熔断(1h)=${exh:-N/A}"

worst_q=$(get_all radar_queue_depth | sort -rn | head -1)
[ "${worst_q:-0}" -gt 100 ] 2>/dev/null && { echo "✗ R4 队列深度峰值 $worst_q > 100（消费停滞）"; WARN=1; } \
  || echo "✓ R4 队列深度峰值 ${worst_q:-N/A}"

failed_1h=$(get 'radar_job_run_1h{status="failed"}')
[ "${failed_1h:-0}" -gt 50 ] 2>/dev/null && { echo "✗ R5 近1h失败任务 $failed_1h > 50"; WARN=1; } \
  || echo "✓ R5 近1h失败任务 ${failed_1h:-N/A}"

if command -v docker >/dev/null 2>&1; then
  for c in streamcap douyin-api douyinlive dtk-postgres; do
    state=$(docker inspect -f "{{.State.Status}}" "$c" 2>/dev/null) || state=missing
    [ "$state" = "running" ] || { echo "✗ R6 引擎容器 $c 状态=$state"; WARN=1; }
  done
  echo "✓ R6 引擎容器已检查"
else
  echo "- R6 跳过（无 docker；在生产机上运行才有容器检查）"
fi
pgrep -f "celery.*radar@prod" >/dev/null 2>&1 && echo "✓ R6 主 worker 存活" || { echo "✗ R6 主 worker 不在"; WARN=1; }
pgrep -f "radar-danmaku@prod" >/dev/null 2>&1 && echo "✓ R6 弹幕 worker 存活" || { echo "✗ R6 弹幕 worker 不在"; WARN=1; }

save http_5xx "$new_5xx" > "$STATE"

[ "$WARN" -eq 0 ] && echo "全部通过" || echo "存在告警，详见上方 ✗ 行"
exit "$WARN"
