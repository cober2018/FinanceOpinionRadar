#!/usr/bin/env bash
# 一键发版到生产机：前端构建 → 代码同步 → 库迁移 → 进程重启 → 冒烟。
# 前提：本机 ~/.ssh 已授权生产机（ssh-copy-id 一次）；容器栈与 venv 已就位。
# 用法：scripts/deploy.sh            （日常发版，约 2 分钟）
#       SKIP_BUILD=1 scripts/deploy.sh   （前端无改动时跳过构建）

set -euo pipefail

PROD_SSH=${PROD_SSH:-mshengran@192.168.31.37}
PROD_DIR=${PROD_DIR:-/Users/mshengran/Documents/project/FinanceOpinionRadar}
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "▶ 1/5 构建前端"
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  (cd "$REPO_ROOT/apps/web" && npm run build >/dev/null) && echo "  build OK"
else
  echo "  跳过（SKIP_BUILD=1）"
fi

echo "▶ 2/5 同步代码"
rsync -az --delete \
  --exclude .venv --exclude node_modules --exclude data --exclude logs \
  --exclude backups --exclude 'celerybeat-schedule*' --exclude __pycache__ \
  --exclude .zcode --exclude .git \
  "$REPO_ROOT/" "$PROD_SSH:$PROD_DIR/"
echo "  rsync OK"

echo "▶ 3/5 库结构迁移（无新迁移则为空跑）"
ssh "$PROD_SSH" "cd '$PROD_DIR' && .venv/bin/python -m alembic -c apps/api/alembic.ini upgrade head | tail -1"

echo "▶ 4/5 重启 API 与 worker（radar@prod，-c 3）"
ssh "$PROD_SSH" "
  export PATH=\$HOME/bin:/usr/local/bin:\$PATH
  pkill -f 'radar@prod' 2>/dev/null || true; sleep 4
  pkill -9 -f 'radar@prod' 2>/dev/null || true
  pkill -f 'uvicorn app.main:app' 2>/dev/null || true; sleep 2
  cd '$PROD_DIR'
  nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8010 --app-dir apps/api >> ~/radar-api.log 2>&1 &
  nohup .venv/bin/python -m celery -A app.worker.celery_app worker --beat \
    -Q default,media,llm,danmaku --concurrency=3 --loglevel=info -n radar@prod \
    >> ~/radar-worker.log 2>&1 &
  echo '  processes restarted'
"

echo "▶ 5/5 冒烟"
sleep 12
ssh "$PROD_SSH" "
  curl -s -o /dev/null -w '  console=%{http_code}\n' http://localhost:8010/console/
  tail -1 ~/radar-worker.log | cut -c1-70
"

echo "✅ 发版完成（${PROD_SSH}）"
