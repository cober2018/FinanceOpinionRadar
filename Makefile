# 财经观点雷达 开发工作流入口 (RAD-001d)
# doctor 的硬性失败项只有 .venv / Python 3.12;docker 项在 Task 5 (docker-compose.yml) 落地前仅 WARN。

PORT ?= 8000
PYTHON ?= .venv/bin/python

.DEFAULT_GOAL := help

.PHONY: help setup dev web stop bootstrap doctor lint format test test-e2e migrate worker seed reset-db

help: ## 显示所有可用目标
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

setup: ## 创建 .venv 并安装后端/前端依赖
	python3.12 -m venv .venv
	$(PYTHON) -m pip install -U pip
	$(PYTHON) -m pip install -e "apps/api[dev]"
	cd apps/web && npm install

dev: ## 启动 API 开发服务器 (前端开发服务器用 make web)
	docker compose up -d
	$(PYTHON) -m uvicorn app.main:app --reload --port $(PORT) --app-dir apps/api

web: ## 启动前端 Vite 开发服务器
	cd apps/web && npm run dev

build-web: ## 构建前端产物（apps/web/dist），此后 API 单端口同时服务页面与 /api
	cd apps/web && npm run build

stop: ## 停止 docker compose 服务
	docker compose stop

bootstrap: ## 一键初始化本地环境 (启动依赖 + 迁移 + 种子数据, 幂等)
	docker compose up -d --wait && $(MAKE) migrate && $(MAKE) seed

doctor: ## 检查本地开发环境 (docker 项在 Task 5 前仅 WARN)
	@fail=0; \
	if [ -x "$(PYTHON)" ]; then echo "  PASS .venv 已就绪"; else echo "  FAIL .venv 缺失 — 运行: make setup"; fail=1; fi; \
	pyv="$$($(PYTHON) --version 2>&1)"; \
	case "$$pyv" in *"Python 3.12"*) echo "  PASS $$pyv";; *) echo "  FAIL $$pyv — 需要 Python 3.12, 运行: make setup"; fail=1;; esac; \
	nodev="$$(node --version 2>/dev/null || true)"; \
	if [ -n "$$nodev" ]; then node_major="$${nodev#v}"; node_major="$${node_major%%.*}"; \
	if [ "$$node_major" -ge 22 ] 2>/dev/null; then echo "  PASS node $$nodev"; else echo "  WARN node $$nodev 低于 22 — 升级: nvm install 22"; fi; \
	else echo "  WARN node 未安装 — 安装: nvm install 22"; fi; \
	docker_up=0; \
	if docker info >/dev/null 2>&1; then echo "  PASS docker daemon 运行中"; docker_up=1; \
	elif [ -f docker-compose.yml ]; then echo "  FAIL docker daemon 未运行 — 启动 Docker Desktop"; fail=1; \
	else echo "  WARN docker daemon 未运行 — 启动 Docker Desktop"; fi; \
	if [ ! -f docker-compose.yml ]; then echo "  WARN docker-compose.yml 尚未创建 (Task 5), 可忽略"; \
	elif [ "$$docker_up" -ne 1 ]; then echo "  WARN compose 服务未启动 — 先启动 Docker Desktop"; \
	elif docker compose ps -q 2>/dev/null | grep -q .; then echo "  PASS docker compose 服务已启动"; \
	else echo "  FAIL compose 服务未启动 — 运行: make bootstrap"; fail=1; fi; \
	if [ "$$fail" -ne 0 ]; then echo "存在 FAIL 项, 请按上方提示修复"; exit 1; fi; \
	echo "本地环境检查通过"

lint: ## 运行 ruff + mypy 静态检查
	$(PYTHON) -m ruff check apps/api tests scripts
	$(PYTHON) -m mypy --config-file apps/api/pyproject.toml apps/api/app

format: ## 用 ruff 格式化后端代码
	$(PYTHON) -m ruff format apps/api tests scripts

test: ## 运行 pytest 测试
	$(PYTHON) -m pytest

test-e2e: ## 端到端测试 (推迟到 EPIC-08)
	@echo "E2E deferred until EPIC-08 (per execution plan step 5)"

migrate: ## 执行数据库迁移至最新版本
	$(PYTHON) -m alembic -c apps/api/alembic.ini upgrade head

worker: ## 启动 Celery worker
	$(PYTHON) -m celery -A app.worker.celery_app worker --loglevel=info

worker-beat: ## 启动 Celery worker + beat（来源发现调度）
	$(PYTHON) -m celery -A app.worker.celery_app worker --beat --loglevel=info

live-status: ## 直播值守看板（每个值守直播间一行：在播/同步/会话/转录）
	$(PYTHON) scripts/live_dashboard.py

seed: ## 写入开发用种子数据
	$(PYTHON) scripts/seed_dev.py

reset-db: ## 销毁性: 清空全部本地数据卷并重建, 数据不可恢复, 谨慎使用
	docker compose down -v && docker compose up -d --wait && $(MAKE) migrate && $(MAKE) seed
