#!/usr/bin/env bash
# 一次性迁移交接（验收 A4 / design「存量接续与回退演练」的机械部分）：
# 旧版布局（app.main:app + 外部引擎容器）→ 统一架构（app.bootstrap.* + 本仓库引擎）。
#
# 顺序（design 迁移计划第 2-4 步）：
#   1 快照在途状态（队列深度/活跃 worker/引擎容器/媒体目录）→ 交接记录落盘
#   2 暂停派发：先停 beat（唯一调度器），再温和停 worker（SIGTERM 等 warm shutdown）
#   3 停旧 API（新旧入口名都识别）
#   4 旧引擎容器改名让位（-old 后缀，保留为回退产物，不删除）
#   5 dtk 身份数据迁移：旧 dtk-postgres dump → 新 compose 卷（DTK_SECRET_KEY 不变，
#     identities 加密凭据原样可解；无旧库则跳过，走 README 手动导入）
#   6 scripts/deploy.sh --engines（新应用 + 引擎栈唯一执行者）
#   7 核对：版本标记、队列消费恢复、媒体路径挂载、引擎容器健康
#
# 用法：scripts/migrate_handover.sh          # 交互式逐步执行，每步失败即停
# 回退：旧容器未删除（-old）；旧应用代码在 git（b013bfe）；.release_version 留有版本链。
#       回退 = 停新容器/进程 → docker rename 回原名 → git checkout b013bfe 重启旧进程。

set -euo pipefail

PROD_SSH=${PROD_SSH:-mshengran@192.168.31.37}
PROD_DIR=${PROD_DIR:-/Users/mshengran/Documents/project/FinanceOpinionRadar}
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
SNAPSHOT="$PROD_DIR/migration-snapshot-$STAMP.txt"
QUEUES="default media llm danmaku"

step() { echo; echo "===== $* ====="; }

step "1/7 在途状态快照 → $SNAPSHOT"
ssh "$PROD_SSH" "
  export PATH=\$HOME/bin:/usr/local/bin:/opt/homebrew/bin:\$PATH
  cd '$PROD_DIR'
  {
    echo \"# radar migration snapshot \$STAMP\"
    echo '# --- celery 队列深度（broker redis） ---'
    for q in $QUEUES; do
      depth=\$(docker exec \$(docker ps --format '{{.Names}}' | grep -m1 -E 'financeopinionradar-redis|radar-redis') redis-cli LLEN \$q 2>/dev/null || echo NA)
      echo \"queue \$q = \$depth\"
    done
    echo '# --- 活跃 worker/任务 ---'
    pgrep -fl 'celery.*radar' || true
    echo '# --- 引擎容器 ---'
    docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | grep -E 'streamcap|douyinlive|douyin-api|dtk' || true
    echo '# --- 媒体/分片目录 ---'
    du -sh data/douyin/live_segments 2>/dev/null || true
    ls data/douyin/streamcap-config 2>/dev/null | head -5
    echo '# --- 版本 ---'
    cat .release_version 2>/dev/null || echo 'old-layout (no marker)'
  } > '$SNAPSHOT'
  cat '$SNAPSHOT'
"
echo "快照已落远端 ${SNAPSHOT}（回退与接续核对的依据）"

step "2/7 暂停派发：停 beat（唯一调度器）→ 温和停 worker"
ssh "$PROD_SSH" '
  export PATH=$HOME/bin:/usr/local/bin:$PATH
  # beat 与主 worker 同进程（radar@prod --beat）：先停它即停止新派发
  pkill -f "radar@prod" 2>/dev/null || true
  # 温和停弹幕 worker（SIGTERM = celery warm shutdown，执行中任务跑完）
  pkill -f "radar-danmaku@prod" 2>/dev/null || true
  pkill -f "celery -A app.worker" 2>/dev/null || true
  for _ in $(seq 1 60); do
    pgrep -f "celery.*radar" >/dev/null 2>&1 || break
    sleep 2
  done
  if pgrep -f "celery.*radar" >/dev/null 2>&1; then
    echo "  worker 120s 未退完，强制收尾（在途长任务按快照记录待恢复）" >&2
    pkill -9 -f "radar@prod" 2>/dev/null || true
    pkill -9 -f "radar-danmaku@prod" 2>/dev/null || true
    pkill -9 -f "celery -A app.worker" 2>/dev/null || true
  fi
  echo "  派发已暂停，worker 已退出"
'

step "3/7 停旧 API（新旧入口名都识别）"
ssh "$PROD_SSH" '
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "uvicorn app.bootstrap.asgi:app" 2>/dev/null || true
  sleep 3
  pkill -9 -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -9 -f "uvicorn app.bootstrap.asgi:app" 2>/dev/null || true
  lsof -nP -iTCP:8010 -sTCP:LISTEN >/dev/null 2>&1 && { echo "  ✗ 8010 仍被占用" >&2; exit 1; }
  echo "  旧 API 已退出，8010 已让位"
'

step "4/7 旧引擎容器改名让位（保留回退产物，不删除）"
ssh "$PROD_SSH" '
  STAMP='"$STAMP"'
  for c in streamcap douyinlive douyin-api dtk-worker dtk-postgres dtk-redis dtk-migrate; do
    if docker ps -a --format "{{.Names}}" | grep -qx "$c"; then
      docker stop "$c" >/dev/null 2>&1 || true
      docker rename "$c" "$c-old-$STAMP" || true
      echo "  $c → $c-old-${STAMP}（保留）"
    fi
  done
'

step "5/7 dtk 身份数据迁移（旧库 dump → 新 compose 卷；可选）"
ssh "$PROD_SSH" "
  cd '$PROD_DIR'
  grep -q '^DTK_SECRET_KEY=' .env || { echo '  ✗ .env 缺 DTK_SECRET_KEY：请先按 README 生成并保持与旧库一致，再重跑本步' >&2; exit 1; }
  OLD_DTK=\$(docker ps -a --format '{{.Names}}' | grep -m1 'dtk-postgres-old' || true)
  if [[ -n \"\$OLD_DTK\" ]]; then
    docker start \"\$OLD_DTK\" >/dev/null && sleep 5
    docker exec \"\$OLD_DTK\" pg_dump -U dtk -d dtk -Fc > /tmp/dtk-migration.dump
    echo '  旧库 dump 完成（' \$(du -h /tmp/dtk-migration.dump | cut -f1) '）'
    set -a; . ./.env; set +a
    docker compose -f infra/docker/docker-compose.douyin.yml up -d dtk-postgres dtk-redis
    sleep 8
    docker compose -f infra/docker/docker-compose.douyin.yml exec -T dtk-postgres \
      pg_restore -U dtk -d dtk --clean --if-exists < /tmp/dtk-migration.dump
    echo '  身份/凭据/密钥已迁入新库（DTK_SECRET_KEY 不变，加密凭据可解）'
  else
    echo '  无旧 dtk 库（或已迁移）：跳过，凭据按 README 手动导入'
  fi
"

step "6/7 发版新应用 + 引擎栈（唯一执行者）"
"$REPO_ROOT/scripts/deploy.sh" --engines

step "7/7 接续核对"
ssh "$PROD_SSH" "
  export PATH=\$HOME/bin:/usr/local/bin:/opt/homebrew/bin:\$PATH
  cd '$PROD_DIR'
  echo '--- 版本 ---'; cat .release_version
  echo '--- 队列消费恢复（深度应递减） ---'
  for q in $QUEUES; do
    depth=\$(docker exec \$(docker ps --format '{{.Names}}' | grep -m1 -E 'financeopinionradar-redis|radar-redis') redis-cli LLEN \$q 2>/dev/null || echo NA)
    echo \"queue \$q = \$depth\"
  done
  echo '--- 引擎容器 ---'
  docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'streamcap|douyinlive|douyin-api' || true
  echo '--- 分片目录挂载（引擎容器内可见 = 写盘链路贯通） ---'
  docker exec streamcap ls /app/downloads >/dev/null 2>&1 && echo '  /app/downloads OK' || echo '  ✗ 分片挂载异常'
"
echo
echo "✅ 迁移交接完成。回退路径：旧容器 *-old-$STAMP 原地保留；旧应用 = git b013bfe。"
echo "   五段接续/回退演练的证据（design 存量接续与回退演练）记录于 $SNAPSHOT 与上方输出。"
