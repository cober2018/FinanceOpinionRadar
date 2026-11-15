#!/usr/bin/env bash
# 一键发版到生产机：预检 → 前端构建 → 代码同步 → 库迁移 → 进程交接 → 冒烟。
# 前提：本机 ~/.ssh 已授权生产机（ssh-copy-id 一次）；容器栈与 venv 已就位。
# 用法：scripts/deploy.sh                （应用发版：API + 双 worker，约 2 分钟）
#       SKIP_BUILD=1 scripts/deploy.sh   （前端无改动时跳过构建）
#       scripts/deploy.sh --engines      （同时把引擎栈构建/切换到本仓库镜像；
#                                         需生产 .env 提供 DTK_SECRET_KEY。首次从旧
#                                         外部引擎容器切换前，先跑 migrate_handover.sh）
#
# 交接红线（验收 A4，2026-11-03）：
#   1) 旧版入口是 uvicorn app.main:app + celery -A app.worker.celery_app；新版是
#      app.bootstrap.*。两套名字都识别，首次升级不漏停旧 API（否则旧进程占着
#      8010，新进程起不来或流量仍进旧版）。
#   2) 交接顺序 = 先 rsync 预检新版入口文件齐备 → 才停旧进程；rsync 残缺时不动生产。
#   3) 启动前等 8010 让位（旧进程若未退出则明确报错退出，绝不盲目双起）。
#   4) 冒烟包含版本核对：远端 .release_version 必须等于本地 HEAD（证明发版一致）。
# .env 不随发版覆盖（生产专属配置，历次事故红线）。

set -euo pipefail

PROD_SSH=${PROD_SSH:-mshengran@192.168.31.37}
PROD_DIR=${PROD_DIR:-/Users/mshengran/Documents/project/FinanceOpinionRadar}
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
GIT_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD)
WITH_ENGINES=0
[[ "${1:-}" == "--engines" ]] && WITH_ENGINES=1

echo "▶ 1/6 构建前端"
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  (cd "$REPO_ROOT/apps/web" && npm run build >/dev/null) && echo "  build OK"
else
  echo "  跳过（SKIP_BUILD=1）"
fi

echo "▶ 2/6 同步代码 + 版本标记"
rsync -az --delete \
  --exclude .venv --exclude node_modules --exclude data --exclude logs \
  --exclude backups --exclude 'celerybeat-schedule*' --exclude __pycache__ \
  --exclude .zcode --exclude .git \
  --exclude 'migration-snapshot-*' \
  --exclude .env --exclude .release_version \
  "$REPO_ROOT/" "$PROD_SSH:$PROD_DIR/"
echo "$GIT_SHA $(date '+%F %T')${WITH_ENGINES:+ engines}" \
  | ssh "$PROD_SSH" "cat > '$PROD_DIR/.release_version'"
echo "  rsync OK（本地 HEAD $GIT_SHA）"

echo "▶ 3/6 预检：新版入口齐备才允许交接"
ssh "$PROD_SSH" "cd '$PROD_DIR' && \
  test -f apps/api/app/bootstrap/asgi.py && test -f apps/api/app/bootstrap/celery.py" \
  || { echo "  ✗ 新版入口文件缺失（rsync 不完整？），已中止，未动生产进程" >&2; exit 1; }
echo "  bootstrap/asgi.py + celery.py 在位"

echo "▶ 4/6 库结构迁移（无新迁移则为空跑）"
ssh "$PROD_SSH" "cd '$PROD_DIR' && .venv/bin/python -m alembic -c apps/api/alembic.ini upgrade head | tail -1"

echo "▶ 5/6 进程交接（旧/新入口名都识别；先 TERM 后 KILL；8010 让位校验）"
ssh "$PROD_SSH" '
  export PATH=$HOME/bin:/usr/local/bin:$PATH
  # 停旧版入口（首次升级残留）与新版入口；worker 节点名新旧一致，另按旧模块名兜底
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "uvicorn app.bootstrap.asgi:app" 2>/dev/null || true
  pkill -f "radar-danmaku@prod" 2>/dev/null || true
  pkill -f "radar@prod" 2>/dev/null || true
  pkill -f "celery -A app.worker" 2>/dev/null || true
  sleep 4   # SIGTERM 温和退出窗口（celery warm shutdown / uvicorn 收尾）
  pkill -9 -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -9 -f "uvicorn app.bootstrap.asgi:app" 2>/dev/null || true
  pkill -9 -f "radar-danmaku@prod" 2>/dev/null || true
  pkill -9 -f "radar@prod" 2>/dev/null || true
  pkill -9 -f "celery -A app.worker" 2>/dev/null || true
  # 8010 让位校验：旧进程没退干净就明确失败，绝不盲目双起
  for _ in $(seq 1 15); do
    lsof -nP -iTCP:8010 -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 1
  done
  if lsof -nP -iTCP:8010 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  ✗ 8010 仍被占用（旧进程未退出），发版中止" >&2
    exit 1
  fi
  cd "'"$PROD_DIR"'"
  nohup .venv/bin/uvicorn app.bootstrap.asgi:app --host 0.0.0.0 --port 8010 --app-dir apps/api >> ~/radar-api.log 2>&1 &
  nohup .venv/bin/python -m celery -A app.bootstrap.celery worker \
    -Q default,media,llm --concurrency=3 --loglevel=info -n radar@prod \
    --beat >> ~/radar-worker.log 2>&1 &
  nohup .venv/bin/python -m celery -A app.bootstrap.celery worker \
    -Q danmaku --concurrency=4 --loglevel=info -n radar-danmaku@prod \
    >> ~/radar-danmaku-worker.log 2>&1 &
  sleep 8
  echo "  processes restarted"
'

if [[ $WITH_ENGINES == 1 ]]; then
  echo "▶ 5b/6 引擎栈切换到本仓库镜像（compose build + up）"
  ssh "$PROD_SSH" "
    cd '$PROD_DIR'
    grep -q '^DTK_SECRET_KEY=' .env || { echo '  ✗ 生产 .env 缺 DTK_SECRET_KEY（引擎凭据主密钥）' >&2; exit 1; }
    set -a; . ./.env; set +a
    docker compose -f infra/docker/docker-compose.douyin.yml up -d --build
  " || { echo "  ✗ 引擎切换失败。若是容器名冲突（旧外部容器占用 streamcap/douyinlive 等名字），先跑 scripts/migrate_handover.sh" >&2; exit 1; }
fi

echo "▶ 6/6 冒烟（版本核对 + 进程 + 引擎）"
sleep 12
ssh "$PROD_SSH" "
  pgrep -f 'celery.*radar@prod' >/dev/null || { echo '  ✗ 主 worker 未启动（看 ~/radar-worker.log）' >&2; exit 1; }
  pgrep -f 'radar-danmaku@prod' >/dev/null || { echo '  ✗ 弹幕 worker 未启动（看 ~/radar-danmaku-worker.log）' >&2; exit 1; }
  curl -s -o /dev/null -w '  console=%{http_code}\n' http://localhost:8010/console/
  remote_sha=\$(cut -d' ' -f1 '$PROD_DIR/.release_version')
  echo \"  version: remote=\$remote_sha local=$GIT_SHA\"
  [[ \"\$remote_sha\" == \"$GIT_SHA\" ]] || { echo '  ✗ 版本不一致：远端发版与本地 HEAD 不匹配' >&2; exit 1; }
  tail -1 ~/radar-worker.log | cut -c1-70
  tail -1 ~/radar-danmaku-worker.log | cut -c1-70
  docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'streamcap|douyinlive|douyin-api' || echo '  （引擎容器未切换，--engines 可切换）'
"

echo "✅ 发版完成（${PROD_SSH} @ $GIT_SHA）"
