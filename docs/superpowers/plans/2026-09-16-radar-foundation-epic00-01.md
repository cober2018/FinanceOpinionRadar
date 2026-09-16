<!-- /autoplan restore point: "/Users/mshengran/.gstack/projects/FinanceOpinionRadar/-autoplan-restore-20260916-181843.md" -->
## Implementation plan
# 财经观点雷达 Plan #1：工程基线 + 数据库领域模型（EPIC-00 + EPIC-01）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从零建立 finance-opinion-radar 仓库：可一键启动的工程基线（Docker 依赖 + Make + CI）与 PRD §8 全部 14 张表的数据库领域层（Alembic + SQLAlchemy 2 + Repository + 种子数据）。

**Architecture:** 模块化单体：单一 Python 发行包 `apps/api`（包名 `app`），内部分层 `core / db / domain / repositories / worker`；前端 `apps/web` 为 Vite React-TS 脚手架；测试放在仓库根 `tests/`。数据库约束以迁移为准，enum 用 varchar + Python enum（PRD 8.1 额外要求）。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.x(sync + psycopg3) / Alembic / Celery+Redis / PostgreSQL 16 / MinIO / React+TS+Vite / Vitest / pytest / ruff / mypy / GitHub Actions。

**上游文档:** `01_finance_opinion_radar_PRD.md`（§8 数据模型、§11 状态机、§4 枚举）、`02_finance_opinion_radar_execution_plan.md`（RAD-001~003、RAD-010~013、§1 开发原则、§16 DoD）。

**本计划范围外的关键决策（已定，见 docs/adr/）：**
- ADR-0001：V1 单一 Python 发行包。执行计划 §0.1 的根级 `services/media|transcription|llm` 映射为 `app/services/*`（保持一个 editable install，避免跨目录 import 复杂度）；根级 `services/`、`packages/`、`apps/worker/` 目录推迟到真正需要时创建。
- ADR-0002：SQLAlchemy 用 sync（不是 async）。Celery worker 与 FastAPI 共用同一套 repo 代码，V1 无高并发读需求。
- ADR-0003：Alembic 目录放 `apps/api/migrations/`（执行计划树中 `migrations/` 在根目录，归属 Python 工程更内聚）。

---

## File Structure（本计划创建的文件全景）

```text
FinanceOpinionRadar/
├── .editorconfig / .gitattributes / .gitignore / .pre-commit-config.yaml
├── .env.example
├── pytest.ini
├── Makefile
├── docker-compose.yml
├── README.md
├── apps/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── alembic.ini
│   │   ├── migrations/            # env.py + versions/*.py
│   │   └── app/
│   │       ├── __init__.py
│   │       ├── main.py            # FastAPI /health
│   │       ├── core/settings.py   # pydantic-settings
│   │       ├── db/{__init__,base,session}.py
│   │       ├── db/models/         # creator/source/media/taxonomy/viewpoint/consensus/system
│   │       ├── domain/enums.py    # Stance/Horizon/ChangeType/SourceItemStatus/JobType...
│   │       ├── repositories/      # base/creators/source_accounts/viewpoints/...
│   │       └── worker/celery_app.py
│   └── web/                       # Vite react-ts + vitest 冒烟
├── scripts/seed_dev.py
├── infra/docker/Dockerfile.api
├── .github/workflows/ci.yml
├── docs/adr/0001..0003-*.md
└── tests/
    ├── pytest.ini 由根提供；unit/ 与 integration/
    ├── unit/test_enums.py
    └── integration/{conftest,test_migrations,test_constraints,test_models_utc,test_repositories,test_seed}.py
```

**环境前置（本机已验证存在）：** `python3.12`（/opt/homebrew/bin）、`node 25`、`docker compose v5`、`git`。所有命令默认在仓库根执行。

---

### Task 1: RAD-001a 仓库初始化与基础配置文件

**Files:**
- Create: `.gitignore`, `.editorconfig`, `.gitattributes`, `.env.example`, `README.md`（骨架）

- [ ] **Step 1: git init + 基础文件**

```bash
cd /Users/mshengran/Project/FinanceOpinionRadar
git init -b main
```

`.gitignore`（要点）:

```gitignore
.venv/
__pycache__/
*.pyc
.env
.env.*
!.env.example
node_modules/
dist/
.pytest_cache/
.ruff_cache/
.mypy_cache/
*.egg-info/
.DS_Store
```

`.editorconfig`: root=true, utf-8, lf, indent 4 (py) / 2 (ts,json,css,yml)。

`.gitattributes`: `* text=auto eol=lf`，`*.png binary`。

`.env.example`:

```dotenv
# --- Database (docker compose 默认值) ---
DATABASE_URL=postgresql+psycopg://radar:radar@localhost:5432/radar
# --- Redis ---
REDIS_URL=redis://localhost:6379/0
# --- Object Storage (MinIO dev) ---
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=radar
S3_SECRET_KEY=radar-secret
S3_BUCKET_MEDIA=radar-media
# --- LLM Provider (V1 默认可留空) ---
LLM_API_KEY=
LLM_BASE_URL=
```

- [ ] **Step 2: 验证 `git status` 干净、`git check-ignore .env` 生效**

- [ ] **Step 3: Commit** `git add -A && git commit -m "chore: initialize repository baseline (RAD-001)"`

---

### Task 2: RAD-001b apps/web Vite 脚手架 + 冒烟测试

**Files:**
- Create: `apps/web/**`（Vite 模板生成 + `src/App.test.tsx` + `vitest` 配置）

- [ ] **Step 1: 生成模板**

```bash
npm create vite@latest apps/web -- --template react-ts
cd apps/web && npm install
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom
```

- [ ] **Step 2: 写冒烟测试 `apps/web/src/App.test.tsx`**

```tsx
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("App", () => {
  it("renders without crashing", () => {
    const { container } = render(<App />);
    expect(container.childElementCount).toBeGreaterThan(0);
  });
});
```

`package.json` 增加: `"test": "vitest run"`；`vite.config.ts` 增加 `test: { environment: "jsdom" }`（需 `/// <reference types="vitest" />`）。

- [ ] **Step 3: 验证** `npm run test` PASS、`npm run build` 成功

- [ ] **Step 4: Commit** `feat: scaffold web app with vitest smoke test (RAD-001)`

---

### Task 3: RAD-001c apps/api Python 工程 + /health（TDD）

**Files:**
- Create: `apps/api/pyproject.toml`, `apps/api/app/__init__.py`, `apps/api/app/core/settings.py`, `apps/api/app/main.py`, `pytest.ini`（根）, `tests/unit/__init__.py` 可省, `tests/unit/test_health.py`

- [ ] **Step 1: 安装依赖（venv + editable）**

```bash
python3.12 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e "apps/api[dev]"
```

`apps/api/pyproject.toml`:

```toml
[project]
name = "radar-api"
version = "0.1.0"
description = "Finance Opinion Radar API"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "sqlalchemy>=2.0.30",
  "alembic>=1.13",
  "psycopg[binary]>=3.2",
  "celery[redis]>=5.4",
  "redis>=5.0",
  "httpx>=0.27",
  "structlog>=24.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.2", "pytest-cov>=5.0", "ruff>=0.5", "mypy>=1.10"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
```

根 `pytest.ini`:

```ini
[pytest]
testpaths = tests
addopts = -q
```

- [ ] **Step 2: 写失败测试 `tests/unit/test_health.py`**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 3: 运行验证失败** `.venv/bin/pytest tests/unit/test_health.py -v` → FAIL（ModuleNotFoundError: app.main）

- [ ] **Step 4: 最小实现**

`apps/api/app/core/settings.py`:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置：仅从环境变量 / .env 注入，禁止硬编码连接串。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://radar:radar@localhost:5432/radar"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "radar"
    s3_secret_key: str = "radar-secret"
    s3_bucket_media: str = "radar-media"
    llm_api_key: str = ""
    llm_base_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`apps/api/app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="Finance Opinion Radar", version="0.1.0")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

（补 `apps/api/app/__init__.py`、`apps/api/app/core/__init__.py` 空文件）

- [ ] **Step 5: 验证通过** `.venv/bin/pytest tests/unit/test_health.py -v` → PASS

- [ ] **Step 6: Commit** `feat: api project skeleton with health endpoint (RAD-001)`

---

### Task 4: RAD-001d Makefile + pre-commit

**Files:**
- Create: `Makefile`, `.pre-commit-config.yaml`

`Makefile`（执行计划 RAD-001 最低命令集，worker 目标指向最小 Celery app，Task 7 落地）:

```make
PYTHON ?= .venv/bin/python

.PHONY: setup dev stop lint format test test-e2e migrate worker seed

setup:
	python3.12 -m venv .venv
	$(PYTHON) -m pip install -U pip
	$(PYTHON) -m pip install -e "apps/api[dev]"
	cd apps/web && npm install

dev:
	docker compose up -d
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000 --app-dir apps/api

stop:
	docker compose stop

lint:
	$(PYTHON) -m ruff check apps/api tests scripts
	$(PYTHON) -m mypy apps/api/app

format:
	$(PYTHON) -m ruff format apps/api tests scripts

test:
	$(PYTHON) -m pytest

test-e2e:
	@echo "E2E deferred until EPIC-08 (per execution plan step 5)"

migrate:
	$(PYTHON) -m alembic -c apps/api/alembic.ini upgrade head

worker:
	$(PYTHON) -m celery -A app.worker.celery_app worker --loglevel=info

seed:
	$(PYTHON) scripts/seed_dev.py
```

`.pre-commit-config.yaml`: ruff-pre-commit（`ruff-format` + `ruff check --fix`）+ 基础 hooks（end-of-file-fixer / trailing-whitespace / check-yaml）。

- [ ] **Step 1: 验证** `make lint` 成功（此时无 lint 违例）、`make test` PASS
- [ ] **Step 2: Commit** `chore: add Makefile and pre-commit (RAD-001)`

---

### Task 5: RAD-002 Docker 本地依赖

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: 写 `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: radar
      POSTGRES_PASSWORD: radar
      POSTGRES_DB: radar
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U radar -d radar"]
      interval: 5s
      timeout: 3s
      retries: 12

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 12

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: radar
      MINIO_ROOT_PASSWORD: radar-secret
    ports: ["9000:9000", "9001:9001"]
    volumes: ["miniodata:/data"]
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      timeout: 3s
      retries: 12

  minio-init:
    image: minio/mc:latest
    depends_on:
      minio: { condition: service_healthy }
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 radar radar-secret &&
      mc mb --ignore-existing local/radar-media &&
      echo 'bucket radar-media ready'"

volumes:
  pgdata:
  miniodata:
```

- [ ] **Step 2: 验证 Done 标准** `docker compose up -d && sleep 5 && docker compose ps` → 四个服务全部 `healthy`（minio-init 为 exited 0）

- [ ] **Step 3: Commit** `chore: add docker compose dev dependencies (RAD-002)`

---

### Task 6: RAD-010 Alembic 初始化 + DB session + 空 revision

**Files:**
- Create: `apps/api/alembic.ini`, `apps/api/migrations/env.py`, `apps/api/migrations/script.py.mako`, `apps/api/migrations/versions/`（空 revision 由 Task 8 的 autogenerate 取代，本任务只建基础设施）, `apps/api/app/db/__init__.py`, `apps/api/app/db/base.py`, `apps/api/app/db/session.py`

- [ ] **Step 1: 初始化** `cd apps/api && ../../.venv/bin/python -m alembic init migrations`，然后调整：

`alembic.ini` 关键行（url 留空，运行时注入）:

```ini
[alembic]
script_location = %(here)s/migrations
prepend_sys_path = .
sqlalchemy.url =
```

`migrations/env.py` 核心改动（online/offline 均从 settings 取 URL，且 import 所有 model 以支持 autogenerate）:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.settings import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401  确保模型注册进 metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
```

`app/db/base.py`:

```python
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

# 命名约定：autogenerate 的 downgrade 依赖确定性约束名
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

`app/db/session.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db() -> Session:
    """FastAPI dependency。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
```

- [ ] **Step 2: 验证** `make migrate`（此时无 revision，幂等成功、无报错）
- [ ] **Step 3: Commit** `feat: alembic infrastructure and db session (RAD-010)`

---

### Task 7: 领域枚举（TDD）+ 最小 Celery app

**Files:**
- Create: `apps/api/app/domain/__init__.py`, `apps/api/app/domain/enums.py`, `apps/api/app/worker/__init__.py`, `apps/api/app/worker/celery_app.py`
- Test: `tests/unit/test_enums.py`

- [ ] **Step 1: 写失败测试 `tests/unit/test_enums.py`**（枚举值 = PRD §4.3/§4.4/§11/§7.9 的机器可读契约）

```python
from app.domain.enums import (
    ChangeType,
    DiscoveryMode,
    Horizon,
    ItemType,
    JobStatus,
    JobType,
    SourceItemStatus,
    Stance,
    VerificationStatus,
)


def test_stance_matches_prd() -> None:
    assert {s.value for s in Stance} == {
        "strong_bullish", "bullish", "neutral", "bearish", "strong_bearish", "unclear",
    }


def test_change_type_matches_prd() -> None:
    assert {c.value for c in ChangeType} == {
        "new_thesis", "strengthening", "weakening", "stance_flip",
        "horizon_change", "repeated", "expired", "unclear",
    }


def test_horizon_matches_prd() -> None:
    assert {h.value for h in Horizon} == {"intraday", "1-3D", "1-4W", "1-3M", "3M+"}


def test_source_item_status_state_machine() -> None:
    assert {s.value for s in SourceItemStatus} == {
        "discovered", "resolved", "media_ready", "transcribing", "transcribed",
        "extracting", "reviewing", "ready", "failed", "ignored",
    }


def test_job_types_match_prd_p09() -> None:
    assert {j.value for j in JobType} == {
        "DISCOVER", "RESOLVE_MEDIA", "DOWNLOAD_MEDIA", "FETCH_SUBTITLE", "TRANSCRIBE",
        "ALIGN", "CHUNK", "EXTRACT_VIEWPOINT", "NORMALIZE_ENTITY", "REVIEW_VIEWPOINT",
        "BUILD_SNAPSHOT", "BUILD_CONSENSUS",
    }


def test_verification_status_values() -> None:
    assert {v.value for v in VerificationStatus} == {
        "candidate", "auto_verified", "review_required", "reviewed", "rejected",
    }
```

- [ ] **Step 2: 运行验证失败** → FAIL（ImportError）

- [ ] **Step 3: 实现 `app/domain/enums.py`**

```python
"""领域枚举：数据库存 varchar，应用层用本文件校验（PRD 8.1 额外要求）。"""

from enum import Enum


class Stance(str, Enum):
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"
    UNCLEAR = "unclear"


class Horizon(str, Enum):
    INTRADAY = "intraday"
    ONE_TO_THREE_DAYS = "1-3D"
    ONE_TO_FOUR_WEEKS = "1-4W"
    ONE_TO_THREE_MONTHS = "1-3M"
    THREE_MONTHS_PLUS = "3M+"


class ChangeType(str, Enum):
    NEW_THESIS = "new_thesis"
    STRENGTHENING = "strengthening"
    WEAKENING = "weakening"
    STANCE_FLIP = "stance_flip"
    HORIZON_CHANGE = "horizon_change"
    REPEATED = "repeated"
    EXPIRED = "expired"
    UNCLEAR = "unclear"


class SourceItemStatus(str, Enum):
    DISCOVERED = "discovered"
    RESOLVED = "resolved"
    MEDIA_READY = "media_ready"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    EXTRACTING = "extracting"
    REVIEWING = "reviewing"
    READY = "ready"
    FAILED = "failed"
    IGNORED = "ignored"


class ItemType(str, Enum):
    VOD = "vod"
    LIVE = "live"


class DiscoveryMode(str, Enum):
    MANUAL = "manual"
    AUTO_POLL = "auto_poll"


class VerificationStatus(str, Enum):
    CANDIDATE = "candidate"
    AUTO_VERIFIED = "auto_verified"
    REVIEW_REQUIRED = "review_required"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class JobType(str, Enum):
    DISCOVER = "DISCOVER"
    RESOLVE_MEDIA = "RESOLVE_MEDIA"
    DOWNLOAD_MEDIA = "DOWNLOAD_MEDIA"
    FETCH_SUBTITLE = "FETCH_SUBTITLE"
    TRANSCRIBE = "TRANSCRIBE"
    ALIGN = "ALIGN"
    CHUNK = "CHUNK"
    EXTRACT_VIEWPOINT = "EXTRACT_VIEWPOINT"
    NORMALIZE_ENTITY = "NORMALIZE_ENTITY"
    REVIEW_VIEWPOINT = "REVIEW_VIEWPOINT"
    BUILD_SNAPSHOT = "BUILD_SNAPSHOT"
    BUILD_CONSENSUS = "BUILD_CONSENSUS"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

`app/worker/celery_app.py`（EPIC-02 才有真实任务，这里只建立进程入口）:

```python
from celery import Celery

from app.core.settings import get_settings

celery_app = Celery("radar", broker=get_settings().redis_url, backend=get_settings().redis_url)
celery_app.conf.task_default_queue = "default"
```

- [ ] **Step 4: 验证通过** `.venv/bin/pytest tests/unit/test_enums.py -v` → PASS（6 个）
- [ ] **Step 5: Commit** `feat: domain enums and minimal celery app (RAD-010)`

---

### Task 8: RAD-011 ORM 模型 + autogenerate 迁移（一）：creator / source_account / source_item

**Files:**
- Create: `apps/api/app/db/models/__init__.py`, `creator.py`, `source.py`, `media.py`, `taxonomy.py`, `viewpoint.py`, `consensus.py`, `system.py`
- Test: `tests/integration/conftest.py`, `tests/integration/test_migrations.py`, `tests/integration/test_constraints.py`

> 本任务先建全部 14 张表的模型文件（一次 autogenerate 一次迁移，保证单一 head），但测试分两批验收。conftest 负责建 `radar_test` 库并跑 `alembic upgrade head`。

- [ ] **Step 1: 写集成测试基建 `tests/integration/conftest.py`**

```python
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
ALL_TABLES = (
    "creator, source_account, source_item, media_asset, transcript_segment, topic, entity, "
    "viewpoint, viewpoint_evidence, creator_topic_snapshot, topic_consensus_daily, "
    "prompt_version, job_run, audit_log"
)


def _test_db_url() -> str:
    return os.environ.get("RADAR_TEST_DATABASE_URL",
                          "postgresql+psycopg://radar:radar@localhost:5432/radar_test")


@pytest.fixture(scope="session")
def database_url() -> str:
    return _test_db_url()


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> Iterator[str]:
    admin_url = database_url.rsplit("/", 1)[0] + "/radar"
    admin = create_engine(admin_url)
    with admin.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text("DROP DATABASE IF EXISTS radar_test"))
        conn.execute(text("CREATE DATABASE radar_test"))
    admin.dispose()

    cfg = Config(str(REPO_ROOT / "apps" / "api" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    yield database_url


@pytest.fixture
def db_session(migrated_db: str) -> Iterator[Session]:
    engine = create_engine(migrated_db)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.rollback()
    session.close()
    with engine.connect() as conn:
        conn.execute(text(f"TRUNCATE {ALL_TABLES} RESTART IDENTITY CASCADE"))
    engine.dispose()
```

- [ ] **Step 2: 写失败测试 `tests/integration/test_migrations.py`**

```python
from alembic import command
from alembic.config import Config
from pathlib import Path
from sqlalchemy import create_engine, inspect

EXPECTED_TABLES = {
    "creator", "source_account", "source_item", "media_asset", "transcript_segment",
    "topic", "entity", "viewpoint", "viewpoint_evidence", "creator_topic_snapshot",
    "topic_consensus_daily", "prompt_version", "job_run", "audit_log",
}


def _cfg(database_url: str) -> Config:
    cfg = Config(str(Path(__file__).resolve().parents[2] / "apps" / "api" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def test_upgrade_creates_all_prd_tables(migrated_db: str) -> None:
    insp = inspect(create_engine(migrated_db))
    assert EXPECTED_TABLES <= set(insp.get_table_names())


def test_downgrade_base_then_upgrade(migrated_db: str) -> None:
    command.downgrade(_cfg(migrated_db), "base")
    insp = inspect(create_engine(migrated_db))
    assert not (EXPECTED_TABLES & set(insp.get_table_names()))
    command.upgrade(_cfg(migrated_db), "head")
    insp = inspect(create_engine(migrated_db))
    assert EXPECTED_TABLES <= set(insp.get_table_names())
```

- [ ] **Step 3: 运行验证失败** → FAIL（表不存在）

- [ ] **Step 4: 实现全部 14 张表模型**

`app/db/models/creator.py`:

```python
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class Creator(TimestampMixin, IdMixin, Base):
    __tablename__ = "creator"

    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
```

`app/db/models/source.py`:

```python
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class SourceAccount(TimestampMixin, IdMixin, Base):
    __tablename__ = "source_account"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_source_account_platform_external"),)

    creator_id: Mapped[int] = mapped_column(ForeignKey("creator.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(Text)
    discovery_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    poll_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class SourceItem(TimestampMixin, IdMixin, Base):
    __tablename__ = "source_item"
    __table_args__ = (UniqueConstraint("source_account_id", "external_item_id", name="uq_source_item_account_external"),)

    source_account_id: Mapped[int] = mapped_column(ForeignKey("source_account.id", ondelete="CASCADE"), nullable=False)
    external_item_id: Mapped[str] = mapped_column(String(300), nullable=False)
    item_type: Mapped[str] = mapped_column(String(20), nullable=False, default="vod")
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="discovered")
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
```

`app/db/models/media.py`:

```python
from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class MediaAsset(TimestampMixin, IdMixin, Base):
    __tablename__ = "media_asset"

    source_item_id: Mapped[int] = mapped_column(ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(30), nullable=False)  # video/audio/subtitle
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)


class TranscriptSegment(IdMixin, Base):
    __tablename__ = "transcript_segment"
    __table_args__ = (Index("ix_transcript_segment_item_seq", "source_item_id", "sequence_no"),)

    source_item_id: Mapped[int] = mapped_column(ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False)
    speaker_label: Mapped[str | None] = mapped_column(String(100))
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(20))
    asr_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
```

`app/db/models/taxonomy.py`:

```python
from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class Topic(TimestampMixin, IdMixin, Base):
    __tablename__ = "topic"
    __table_args__ = {"comment": "主题词典：canonical_name 唯一"}

    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    topic_type: Mapped[str] = mapped_column(String(30), nullable=False, default="macro")
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")


class Entity(TimestampMixin, IdMixin, Base):
    __tablename__ = "entity"

    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)  # stock/index/commodity/...
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    symbol: Mapped[str | None] = mapped_column(String(50))
    market: Mapped[str | None] = mapped_column(String(30))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list, server_default="{}")
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
```

`app/db/models/viewpoint.py`:

```python
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class Viewpoint(TimestampMixin, IdMixin, Base):
    __tablename__ = "viewpoint"
    __table_args__ = (
        Index("ix_viewpoint_creator_topic", "creator_id", "topic_id"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("importance >= 0 AND importance <= 1", name="importance_range"),
    )

    creator_id: Mapped[int] = mapped_column(ForeignKey("creator.id", ondelete="CASCADE"), nullable=False)
    source_item_id: Mapped[int] = mapped_column(ForeignKey("source_item.id", ondelete="CASCADE"), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topic.id", ondelete="SET NULL"))
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entity.id", ondelete="SET NULL"))
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    stance: Mapped[str] = mapped_column(String(30), nullable=False)
    horizon: Mapped[str] = mapped_column(String(30), nullable=False)
    conditional: Mapped[bool] = mapped_column(nullable=False, default=False)
    importance: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.5)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, default=0.5)
    change_type: Mapped[str | None] = mapped_column(String(30))
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="candidate")
    extractor_version: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(100))


class ViewpointEvidence(IdMixin, Base):
    __tablename__ = "viewpoint_evidence"

    viewpoint_id: Mapped[int] = mapped_column(ForeignKey("viewpoint.id", ondelete="CASCADE"), nullable=False)
    transcript_segment_id: Mapped[int] = mapped_column(ForeignKey("transcript_segment.id", ondelete="CASCADE"), nullable=False)
    start_ms: Mapped[int] = mapped_column(nullable=False)
    end_ms: Mapped[int] = mapped_column(nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_order: Mapped[int] = mapped_column(nullable=False, default=1)
```

（去掉 viewpoint.py 顶部未用的 DateTime/func import，最终以 ruff 通过为准。）

`app/db/models/consensus.py`:

```python
from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CreatorTopicSnapshot(Base):
    __tablename__ = "creator_topic_snapshot"
    __table_args__ = {"comment": "每人物每主题每日最新有效观点快照（PRD 8.1）"}

    creator_id: Mapped[int] = mapped_column(ForeignKey("creator.id", ondelete="CASCADE"), primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topic.id", ondelete="CASCADE"), primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    latest_viewpoint_id: Mapped[int | None] = mapped_column(BigInteger)
    stance: Mapped[str | None] = mapped_column(String(30))
    horizon: Mapped[str | None] = mapped_column(String(30))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    change_type: Mapped[str | None] = mapped_column(String(30))


class TopicConsensusDaily(Base):
    __tablename__ = "topic_consensus_daily"

    topic_id: Mapped[int] = mapped_column(ForeignKey("topic.id", ondelete="CASCADE"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    creator_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bullish_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    neutral_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bearish_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bullish_ratio: Mapped[float | None] = mapped_column(Numeric(6, 4))
    net_stance_score: Mapped[float | None] = mapped_column(Numeric(6, 4))
    disagreement_score: Mapped[float | None] = mapped_column(Numeric(6, 4))
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
```

`app/db/models/system.py`:

```python
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class PromptVersion(TimestampMixin, IdMixin, Base):
    __tablename__ = "prompt_version"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_prompt_version_name_version"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    checksum: Mapped[str | None] = mapped_column(String(64))


class JobRun(TimestampMixin, IdMixin, Base):
    __tablename__ = "job_run"

    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    source_item_id: Mapped[int | None] = mapped_column(ForeignKey("source_item.id", ondelete="SET NULL"))
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    worker: Mapped[str | None] = mapped_column(String(100))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict | None] = mapped_column(JSONB)


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_log"
    __table_args__ = {"comment": "所有人工修改必须落此表（PRD 13.15）"}

    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_json: Mapped[dict | None] = mapped_column(JSONB)
    after_json: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

`app/db/models/__init__.py` 导出全部模型类（供 alembic env.py 与 repositories 引用）。

- [ ] **Step 5: autogenerate + 应用迁移**

```bash
.venv/bin/python -m alembic -c apps/api/alembic.ini revision --autogenerate -m "create all prd core tables"
make migrate
```

- [ ] **Step 6: 运行 `tests/integration/test_migrations.py`** → PASS

- [ ] **Step 7: Commit** `feat: orm models and initial migration for all 14 prd tables (RAD-011)`

---

### Task 9: 约束 / 级联 / UTC 测试（TDD，约束第二批验收）

**Files:**
- Test: `tests/integration/test_constraints.py`, `tests/integration/test_models_utc.py`

- [ ] **Step 1: 写测试（迁移已建表，此时应直接通过；若有失败说明模型/迁移有缺陷，修复模型而非测试）**

```python
# tests/integration/test_constraints.py
import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Creator, SourceAccount, SourceItem


def _account(creator_id: int) -> SourceAccount:
    return SourceAccount(creator_id=creator_id, platform="youtube", external_id="UC_demo")


def test_source_account_unique_platform_external(db_session) -> None:
    creator = Creator(display_name="A")
    db_session.add(creator)
    db_session.flush()
    db_session.add(_account(creator.id))
    db_session.commit()
    db_session.add(_account(creator.id))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_source_item_unique_account_external_item(db_session) -> None:
    creator = Creator(display_name="A")
    db_session.add(creator)
    db_session.flush()
    account = _account(creator.id)
    db_session.add(account)
    db_session.flush()
    common = dict(source_account_id=account.id, external_item_id="vid_1")
    db_session.add(SourceItem(**common))
    db_session.commit()
    db_session.add(SourceItem(**common))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_fk_missing_creator_rejected(db_session) -> None:
    db_session.add(SourceAccount(creator_id=999999, platform="youtube", external_id="x"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_delete_creator_cascades_to_items(db_session) -> None:
    creator = Creator(display_name="A")
    db_session.add(creator)
    db_session.flush()
    account = _account(creator.id)
    db_session.add(account)
    db_session.flush()
    db_session.add(SourceItem(source_account_id=account.id, external_item_id="v1"))
    db_session.commit()

    db_session.delete(creator)
    db_session.commit()

    assert db_session.query(SourceAccount).count() == 0
    assert db_session.query(SourceItem).count() == 0
```

```python
# tests/unit 集成侧 test_models_utc.py
from datetime import timezone

from sqlalchemy import text

from app.db.models import Creator


def test_timestamps_are_tz_aware(db_session) -> None:
    db_session.add(Creator(display_name="A"))
    db_session.commit()
    raw = db_session.execute(text("SELECT created_at FROM creator")).scalar_one()
    assert raw.tzinfo is not None
    assert raw.tzinfo.utcoffset(raw) is not None
```

- [ ] **Step 2: 运行** `.venv/bin/pytest tests/integration -v` → 全部 PASS（RAD-011 Tests 要求：unique 约束 / FK 约束 / UTC datetime 均覆盖）

- [ ] **Step 3: Commit** `test: constraint, cascade and utc integration tests (RAD-011)`

---

### Task 10: RAD-012 Repository 层

**Files:**
- Create: `apps/api/app/repositories/{__init__,base,creators,source_accounts,viewpoints}.py`
- Test: `tests/integration/test_repositories.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/integration/test_repositories.py
from app.db.models import Creator, SourceAccount
from app.repositories import CreatorRepository, SourceAccountRepository


def test_creator_repo_returns_domain_type(db_session) -> None:
    repo = CreatorRepository(db_session)
    creator = repo.create(display_name="张三")
    db_session.commit()
    fetched = repo.get(creator.id)
    assert isinstance(fetched, Creator)
    assert fetched.display_name == "张三"
    assert repo.get(999999) is None


def test_source_account_repo_upsert_idempotent(db_session) -> None:
    creators = CreatorRepository(db_session)
    accounts = SourceAccountRepository(db_session)
    creator = creators.create(display_name="李四")

    a1 = accounts.upsert_by_external(
        creator_id=creator.id, platform="bilibili", external_id="UID_1", handle="lisi"
    )
    a2 = accounts.upsert_by_external(
        creator_id=creator.id, platform="bilibili", external_id="UID_1", handle="lisi-v2"
    )
    db_session.commit()

    assert a1.id == a2.id
    assert a2.handle == "lisi-v2"          # 更新而非新建
    assert accounts.count() == 1           # 幂等（执行计划 RAD-023 依赖）
```

- [ ] **Step 2: 运行验证失败** → ImportError

- [ ] **Step 3: 实现**

`app/repositories/base.py`:

```python
from collections.abc import Sequence
from typing import Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """所有仓库返回 ORM 领域类型，禁止裸 dict（执行计划 RAD-012）。"""

    model: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, obj_id: int) -> ModelT | None:
        return self.session.get(self.model, obj_id)

    def list_all(self) -> Sequence[ModelT]:
        return self.session.scalars(select(self.model)).all()

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(self.model)) or 0
```

`app/repositories/creators.py`:

```python
from sqlalchemy import select

from app.db.models import Creator
from app.repositories.base import BaseRepository


class CreatorRepository(BaseRepository[Creator]):
    model = Creator

    def create(self, *, display_name: str, bio: str | None = None) -> Creator:
        creator = Creator(display_name=display_name, bio=bio)
        self.session.add(creator)
        self.session.flush()
        return creator

    def get_by_name(self, display_name: str) -> Creator | None:
        return self.session.scalar(select(Creator).where(Creator.display_name == display_name))
```

`app/repositories/source_accounts.py`:

```python
from sqlalchemy import select

from app.db.models import SourceAccount
from app.repositories.base import BaseRepository


class SourceAccountRepository(BaseRepository[SourceAccount]):
    model = SourceAccount

    def upsert_by_external(
        self, *, creator_id: int, platform: str, external_id: str, **kwargs: object
    ) -> SourceAccount:
        """按 (platform, external_id) 幂等 upsert：discover job 的重复回调不产生重复行。"""
        account = self.session.scalar(
            select(SourceAccount).where(
                SourceAccount.platform == platform,
                SourceAccount.external_id == external_id,
            )
        )
        if account is None:
            account = SourceAccount(
                creator_id=creator_id, platform=platform, external_id=external_id, **kwargs  # type: ignore[arg-type]
            )
            self.session.add(account)
        else:
            for key, value in kwargs.items():
                setattr(account, key, value)
        self.session.flush()
        return account
```

`app/repositories/viewpoints.py`（时间线查询的最小面）:

```python
from sqlalchemy import select

from app.db.models import Viewpoint
from app.repositories.base import BaseRepository


class ViewpointRepository(BaseRepository[Viewpoint]):
    model = Viewpoint

    def list_by_creator_topic(self, creator_id: int, topic_id: int) -> list[Viewpoint]:
        stmt = (
            select(Viewpoint)
            .where(Viewpoint.creator_id == creator_id, Viewpoint.topic_id == topic_id)
            .order_by(Viewpoint.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())
```

- [ ] **Step 4: 验证通过** → PASS
- [ ] **Step 5: Commit** `feat: repository layer with typed returns (RAD-012)`

---

### Task 11: RAD-013 种子数据 + make seed

**Files:**
- Create: `scripts/seed_dev.py`
- Test: `tests/integration/test_seed.py`

- [ ] **Step 1: 写失败测试（幂等：跑两遍数量不变）**

```python
# tests/integration/test_seed.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.repositories import CreatorRepository, SourceAccountRepository
from scripts.seed_dev import run_seed

SEED_TOPICS = 5
SEED_ENTITIES = 10
SEED_CREATORS = 3
SEED_ACCOUNTS = 2


def _factory(db_session):
    return sessionmaker(bind=db_session.get_bind())


def test_seed_creates_expected_rows(migrated_db: str) -> None:
    factory = sessionmaker(bind=create_engine(migrated_db))
    run_seed(factory)
    session = factory()
    try:
        assert CreatorRepository(session).count() == SEED_CREATORS
        assert SourceAccountRepository(session).count() == SEED_ACCOUNTS
    finally:
        session.close()


def test_seed_is_idempotent(migrated_db: str) -> None:
    factory = sessionmaker(bind=create_engine(migrated_db))
    run_seed(factory)
    run_seed(factory)
    session = factory()
    try:
        assert CreatorRepository(session).count() == SEED_CREATORS
        assert SourceAccountRepository(session).count() == SEED_ACCOUNTS
    finally:
        session.close()
```

（conftest 的 TRUNCATE 清理同样作用于 seed 写入。）

- [ ] **Step 2: 运行验证失败** → ModuleNotFoundError: scripts

- [ ] **Step 3: 实现 `scripts/seed_dev.py`**（数量与执行计划 RAD-013 一致；无真实 Secret）

```python
"""开发种子数据：3 creators / 5 topics / 10 entities / 2 source accounts。幂等。"""

from sqlalchemy.orm import sessionmaker

from app.db.models import Entity, Topic
from app.repositories import CreatorRepository, SourceAccountRepository

TOPICS = [
    ("AI算力", "sector", ["算力", "AI芯片", "算力基建"]),
    ("黄金", "commodity", ["gold", "金价的"]),
    ("美股大盘", "macro", ["标普", "纳指"]),
    ("A股指数", "macro", ["沪深300", "上证指数"]),
    ("美债利率", "macro", ["10年期美债", "美债收益率"]),
]

ENTITIES = [
    ("stock", "英伟达", "NVDA", "US"),
    ("stock", "台积电", "TSM", "US"),
    ("stock", "微软", "MSFT", "US"),
    ("stock", "苹果", "AAPL", "US"),
    ("stock", "贵州茅台", "600519", "CN"),
    ("stock", "宁德时代", "300750", "CN"),
    ("index", "标普500", "SPX", "US"),
    ("index", "纳斯达克指数", "IXIC", "US"),
    ("index", "沪深300", "000300", "CN"),
    ("commodity", "COMEX黄金", "GC", "GLOBAL"),
]

CREATORS = [("陈观点", "财经主播"), ("王策略", "卖方分析师"), ("刘观察", "独立研究者")]

ACCOUNTS = [
    ("陈观点", "youtube", "UC_demo_chen", "@chen-viewpoint", "https://youtube.com/@chen-viewpoint"),
    ("陈观点", "bilibili", "2233_demo_chen", "陈观点", "https://space.bilibili.com/2233_demo_chen"),
]


def run_seed(factory: sessionmaker) -> None:
    with factory() as session:
        creators = CreatorRepository(session)
        accounts = SourceAccountRepository(session)

        for display_name, bio in CREATORS:
            if creators.get_by_name(display_name) is None:
                creators.create(display_name=display_name, bio=bio)

        for canonical_name, topic_type, aliases in TOPICS:
            exists = session.query(Topic).filter_by(canonical_name=canonical_name).one_or_none()
            if exists is None:
                session.add(Topic(canonical_name=canonical_name, topic_type=topic_type, aliases=aliases))

        for entity_type, name, symbol, market in ENTITIES:
            exists = session.query(Entity).filter_by(canonical_name=name).one_or_none()
            if exists is None:
                session.add(Entity(entity_type=entity_type, canonical_name=name, symbol=symbol, market=market))

        for display_name, platform, external_id, handle, url in ACCOUNTS:
            creator = creators.get_by_name(display_name)
            assert creator is not None
            accounts.upsert_by_external(
                creator_id=creator.id, platform=platform, external_id=external_id,
                handle=handle, url=url, discovery_mode="auto_poll",
            )

        session.commit()
        print("seed done: creators=3 topics=5 entities=10 source_accounts=2")


if __name__ == "__main__":
    from app.db.session import SessionLocal

    run_seed(SessionLocal)
```

- [ ] **Step 4: 验证** `.venv/bin/pytest tests/integration/test_seed.py -v` PASS；`make seed` 输出 seed done（对开发库 radar）
- [ ] **Step 5: Commit** `feat: dev seed data (RAD-013)`

---

### Task 12: RAD-003 CI（GitHub Actions）

**Files:**
- Create: `.github/workflows/ci.yml`, `infra/docker/Dockerfile.api`

`infra/docker/Dockerfile.api`:

```dockerfile
FROM python:3.12-slim
WORKDIR /srv
COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

`.github/workflows/ci.yml`:

```yaml
name: ci
on:
  pull_request:
  push: { branches: [main] }

jobs:
  backend-lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e "apps/api[dev]"
      - run: ruff check apps/api tests scripts
      - run: mypy apps/api/app

  backend-test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env: { POSTGRES_USER: radar, POSTGRES_PASSWORD: radar, POSTGRES_DB: radar }
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U radar" --health-interval 5s
          --health-timeout 3s --health-retries 12
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e "apps/api[dev]"
      - run: pytest

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "22", cache: npm, cache-dependency-path: apps/web/package-lock.json }
      - run: npm ci
        working-directory: apps/web
      - run: npm run lint
        working-directory: apps/web
      - run: npm run test
        working-directory: apps/web
      - run: npm run build
        working-directory: apps/web

  docker-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -f infra/docker/Dockerfile.api .
```

- [ ] **Step 1: 本地等价验证**：`make lint && make test && cd apps/web && npm run lint && npm run test && npm run build && docker build -f ../../infra/docker/Dockerfile.api ../..` 全部通过
- [ ] **Step 2: Commit** `ci: github actions for lint, test, build (RAD-003)`

---

### Task 13: README + ADR + 收尾验证（RAD-001 Done）

**Files:**
- Create: `docs/adr/0001-single-python-distribution.md`, `docs/adr/0002-sync-sqlalchemy.md`, `docs/adr/0003-alembic-location.md`
- Modify: `README.md`

- [ ] **Step 1: ADR 三篇**（每篇：Context / Decision / Consequences，即上文“范围外关键决策”展开）

- [ ] **Step 2: README 按仓库规范补全**：背景（PRD 一句话定义）、结构（目录树）、完成项（RAD-001~003、RAD-010~013）、TODO（下一阶段 EPIC-02+）、关键决策（ADR 链接）、依赖与启动（`make setup` → `docker compose up -d` → `make migrate` → `make seed` → `make dev`）

- [ ] **Step 3: 全量验收（RAD-001 Done 标准 + 本计划 DoD）**

```bash
make lint && make test && make migrate && make seed
cd apps/web && npm run test
```

预期：全部成功；`.venv`、`.env`、`node_modules` 不在 `git status` 中。

- [ ] **Step 4: Commit** `docs: readme and adr for foundation phase`

---

## 执行计划 DoD 对照

| 执行计划要求 | 本计划覆盖 |
|---|---|
| RAD-001 必建文件 / Makefile 8 命令 / Done 三条 | Task 1-4, 13 |
| RAD-002 compose 四服务健康检查 passing | Task 5 |
| RAD-003 六个 CI job | Task 12（backend-lint/backend-test/frontend-lint+test+build-web 合并为 frontend job、docker-build） |
| RAD-010 alembic upgrade head 从空库建库 | Task 6, 8 |
| RAD-011 全部 FK/unique/index/enum 策略 + 四类测试 | Task 7-9 |
| RAD-012 Repository 禁止裸 dict | Task 10 |
| RAD-013 种子数量 3/5/10/2、无 Secret | Task 11 |

## 不在本计划（后续计划）

- EPIC-02（RAD-020~023 Adapter/发现/resolve-url）
- EPIC-03（RAD-030~035 存储/字幕/ASR）
- EPIC-04~05（chunk/抽取/审核）
- EPIC-06~08（时间线/共识/API/前端）
- EPIC-09~10（评估/部署）

## 阶段出口条件（D17/G1 + D18/G2）

**G1 tracer bullet（EPIC-02 开始前执行，2-3 天）：** 用一条真实视频走通 现有字幕 → 1 个 prompt → 1 条 viewpoint（含 evidence 绑定）→ 1 个共识数字 的端到端穿刺。schema 未因此改动则放行进入 EPIC-02；若穿刺逼出 schema 变更，先回到本计划的模型层修订。这是对"6 档 stance / horizon 桶能被 LLM 可靠抽取"这一最大未检验假设的最早验证。

**G2 节奏规则：** 任何纯管线史诗 ≤2 周；其后必须有一个产出数据沉淀（verified viewpoint 历史）或用户可见增量的史诗。护城河 = verified history 的尽早累积，不允许无限铺设管线。

<!-- autoplan-accepted:ceo -->
- D1: CI backend-test 步骤改为 `python -m pytest`；验证=CI 收集成功（修复 scripts.seed_dev import）。
- D2: `tests/unit/test_enums.py` 增 DiscoveryMode/ItemType/JobStatus 值集断言；删除 `tests/integration/test_repositories.py` 未用 import（Creator, SourceAccount）；验证=`ruff check tests` 零 F401 且测试通过。
- D3: Task 1 增建 `scripts/README.md`（说明目录用途）；验证=Task 4 `make lint` 通过（scripts 路径已存在）。
- D4: Task 2 Step 2 增指令："若模板未含 `lint` 脚本，安装 ESLint 并补 `lint` 脚本"；验证=`npm run lint` 在 Task 2 即可运行。
- D5: Task 2 增 `.nvmrc`(22) 与 package.json `engines.node>=22`；CI 保持 node 22；验证=本地 `node -v` 检查 + CI 一致。
- D6: docker-compose 固定 `minio/minio` 与 `minio/mc` 具体发行版本 tag；保留 `mc ready local` healthcheck；验证=重复 `docker compose up` 环境一致。
- D7: `test_enums.py` 模块 docstring 标注"PRD-derived；抽取 spike 后复评"；验证=文件内容。
- D8: 修 `test_models_utc.py` 顶部注释（tests/integration）；`settings.py` docstring 改为"生产环境必须通过环境变量/.env 注入；默认值仅限本地开发"；验证=读文件。
- D9: `viewpoint` 模型增 `as_of_date: Mapped[date | None]`（观点时序=来源发布日），增索引 `ix_viewpoint_creator_topic_asof(creator_id, topic_id, as_of_date)`；`ViewpointRepository.list_by_creator_topic` 改为 `ORDER BY as_of_date DESC NULLS LAST, created_at DESC`；迁移随之生成；验证=新增单测：两条同日插入但 published_at 不同的 source_item，时间线按 as_of_date 排序。
- D10: 新增 `docs/adr/0004-extract-idempotency.md`：EXTRACT_VIEWPOINT 重跑幂等契约=按 (source_item_id, prompt_version, extractor_version) 先删旧 candidate 再插入；验证=文件存在且被 README ADR 清单引用。
- D11: `creator_topic_snapshot.latest_viewpoint_id` 改为 FK(viewpoint.id, ondelete=SET NULL)；验证=迁移含 FK。
- D12: `test_seed.py` 断言 creators=3、accounts=2、topics=5、entities=10 四项；验证=测试通过。
- D13: `test_source_item_status_state_machine` 改名 `test_source_item_status_value_set`；验证=测试名与断言一致。
- D14: `ci.yml` 拆为 backend-lint / backend-test / frontend-lint / frontend-test / build-web / docker-build 六 job；验证=job 名与 RAD-003 一一对应。
- D15: conftest `migrated_db` 在 DROP 前断言 `database_url.rsplit("/",1)[1].endswith("_test")` 否则 fail；验证=新增单测或直接以错误 URL 断言 pytest 失败。
- D16: Makefile 增 `doctor` target（检查 docker/compose up 状态、.venv 存在、python 版本、node 版本）；与 D5 一并生效；验证=`make doctor` 输出各检查项。
- D17/G1: 计划末尾增"阶段出口条件"一节：EPIC-02 开始前执行 2-3 天 tracer bullet（真实视频→现有字幕→1 prompt→1 viewpoint→1 共识数字），schema 未变则放行；此为 Gate 呈报项，默认推荐采纳。
- D18/G2: 同节增"节奏规则"：任何纯管线史诗 ≤2 周，其后必须有数据沉淀或用户可见增量；此为 Gate 呈报项，默认推荐采纳。
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:dx -->
- D19: README quickstart 末步 `curl -fsS localhost:8000/api/v1/health` 期望 `{"status":"ok"}`，并列出 web dev URL（localhost:5173）；验证=README 内容 + 实跑 curl。
- D20: Makefile 增 `bootstrap`（docker compose up -d --wait && make migrate && make seed）；Task 5 Step 2 的 sleep 5 改为 `docker compose up -d --wait` 后 `docker compose ps` 全 healthy；验证=make bootstrap 幂等成功。
- D21: Makefile 增 `web`（cd apps/web && npm run dev）；验证=make web 启动 Vite。
- D22: CEO/DX 全部已接受修订折叠进 Task 1-13 正文（D9 的 as_of_date 入 Task 8 模型代码、D14 六-job CI 入 Task 12、D16 doctor 入 Task 4）；计划头部加"修订已内联"注记；验证=任务正文与 Review record 决策一一对应无悬空项。
- D23: dev 目标保持命名，help 注释注明"启动 API（web 见 make web）"；验证=make help 输出。
- D24: upsert_by_external 签名改显式类型参数（creator_id/platform/external_id/handle/url/discovery_mode/enabled），无 kwargs 无 type: ignore；验证=mypy 通过 + 测试断言 handle 更新。
- D25: Makefile 增 `help`（grep ## 注释风格，各目标补 ## 注释）与 `reset-db`（down -v → up --wait → migrate → seed）；验证=make help 列全目标。
- D26: README（Task 13）增 Troubleshooting 表，至少覆盖 Docker 未运行/端口占用/seed 先于 migrate/.venv 缺失/连接拒绝；doctor 每项 PASS/FAIL 附修复提示；验证=README 审查。
- D27: 根 pytest.ini 增 `pythonpath = .`；所有验证命令统一 `.venv/bin/python -m pytest`；验证=裸 pytest 与 -m pytest 均可收集 scripts.seed_dev。
- D28: Task 6 session.py 改惰性 get_engine()（缓存），连接失败错误含变量名与 .env.example 指引；验证=无 DB 时 import 不崩、首连报可读错误。
- D29: Task 11 seed 的 assert 改 `raise RuntimeError(f"seed: creator {display_name!r} not found")`；验证=代码内容。
- D30: Settings 增 `env: str = "dev"` 与校验（非 dev 且未显式 DATABASE_URL → 校验错误带补救文本）；验证=新增单测 test_settings_env_guard。
- D31: README quickstart 步 0 注明 `cp .env.example .env  # optional; defaults match docker-compose`，加一行交互式 API 文档 URL（localhost:8000/docs）；验证=README 内容。
- D32: Dockerfile.api 两段拷贝（先 pyproject 装依赖，后拷 app 装包）；CI backend 两 job 增 cache: pip；验证=二次 docker build 命中缓存层。
- D33: docker-compose.yml 端口改 `"${POSTGRES_PORT:-5432}:5432"` 等插值，MinIO 凭证改 `${MINIO_ROOT_USER:-radar}`；Makefile `PORT ?= 8000` 用于 dev；.env.example 增可选端口变量注释；验证=POSTGRES_PORT=5544 起服务后 5544 可达。
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- D34: Task 6 session.py 改惰性 get_engine()；首次连接错误信息含 `DATABASE_URL` 与 .env.example 引用；验证=无 DB 时 import 不崩 + 首连报错可读。
- D35: conftest `migrated_db` DROP 前断言 `database_url.rsplit("/",1)[1].endswith("_test")`，否则 `pytest.fail(...)`；新增 test_conftest_name_guard 单测以错误 URL 断言失败；验证=测试通过。
- D36: Task 8 模型层 `viewpoint_evidence` 加 `Index("ix_evidence_viewpoint", "viewpoint_id")` 与 `Index("ix_evidence_segment", "transcript_segment_id")`；`media_asset` 加 `Index("ix_media_asset_source_item", "source_item_id")`；`viewpoint` 加 `Index("ix_viewpoint_entity", "entity_id")` 与 `Index("ix_viewpoint_source_item", "source_item_id")`；autogen 校验发出 CREATE INDEX；验证=migrations/versions/...py 含 5 个 CREATE INDEX。
- D37: Task 8 `creator_topic_snapshot.latest_viewpoint_id` 改为 `ForeignKey("viewpoint.id", ondelete="SET NULL")`；验证=migration 含 FK（greenfield 无数据回填需求）。
- D38: Task 3 根 pytest.ini 增 `pythonpath = .`；验证=CI 裸 pytest 收集通过。
- D39: Task 10 `SourceAccountRepository.upsert_by_external` 改用 `from sqlalchemy.dialects.postgresql import insert as pg_insert; stmt = pg_insert(SourceAccount).values(...).on_conflict_do_update(index_elements=["platform","external_id"], set_={...})`；测试增"并发调用不抛 IntegrityError"断言（实际并发用 threading 起 4 个线程）；验证=mypy 通过 + 测试通过。
- D40: Task 10 测试增 `test_viewpoint_repo_orders_by_asof`：构造 2 个 source_item 不同 published_at，各生成 1 个 viewpoint；查询 `list_by_creator_topic` 返回顺序按 as_of_date DESC NULLS LAST；验证=测试通过。
- D41: Task 8 viewpoint.py 模型代码块删除未用 import；任务 8 Step 6 commit 前执行 `make lint` 通过；验证=CI 红→绿。
- D42: AuditLog 表保留定义，**推迟 write 服务实现至 EPIC-05**；新增 ADR-0006 `docs/adr/0006-audit-log-write-deferral.md` 记录推迟原因与 EPIC-05 触发条件；验证=文件存在且 README ADR 清单引用。Taste→Gate 项。
- D43: Task 12 ci.yml backend-test service 移除 redis；README 不再称 backend-test 需要 Redis；验证=CI 仍 green。
- D44: Task 3 Settings 增 `env: str = "dev"` 与 validator（非 dev 且未显式 DATABASE_URL → 校验错误带补救文本）；测试 `tests/unit/test_settings_env_guard.py` 覆盖；验证=单测 PASS。
- D45: Task 3 pyproject.toml `[tool.mypy]` 下增 `[[tool.mypy.overrides]] module = ["celery.*", "redis.*", "pydantic_settings"]` 走 `ignore_missing_imports = true`；保留全局 fallback；验证=mypy 通过。
- D46: README Troubleshooting 表（Task 13 D26）增条目"CREATE DATABASE 权限被拒"：需 `radar` 角色具 `CREATEDB`；conftest `migrated_db` 启动前 ping admin URL 失败时报可读错误；验证=README 内容。
- D47: Task 8 `viewpoint_evidence.start_ms/end_ms` 改 BigInteger；验证=autogen migration 类型为 bigint。
- D48: Task 10 repos 返回类型统一 `list[ModelT]`；验证=mypy 通过。
- D49: Task 11 seed_dev.py 改用 2.x `session.scalar(select(Topic).where(...))`；验证=runs 且 ruff check 通过。
- D50: 14 张表的 JSONB 列（metadata_json/config_json/audit_log.before_json/after_json）加模块 docstring 描述期望形状；正式 schema 推迟 EPIC-04（pydantic 抽取模型落地时）；验证=代码内容。Taste→Gate 项。
- D51: Task 5 compose 固定 `minio/minio:RELEASE.2025-09-15T17-33-27Z` 与 `minio/mc:RELEASE.2025-09-15T17-33-27Z`（或更新稳定版）；验证=重复 up 环境一致。
- D52: Dockerfile.api 两段拷贝（先 pyproject 装依赖，后拷 app 装包）；CI backend 两 job 增 `cache: pip`；验证=二次 docker build 命中缓存层。
- D53: Settings 增 `db_pool_size: int = 5` + `db_max_overflow: int = 10`；engine 用 settings；ADR-0005 follow-up 记录 sync→async 切换量化阈值（具体 RPS/并发留空，触发条件描述）；验证=Settings 生效。
- D54: Task 8 `creator_topic_snapshot` 与 `topic_consensus_daily` 各增 `computed_at`；验证=autogen migration 含列。
- D55: tests/integration 抽 `_constants.py` 含 `ALL_TABLES` 与 `EXPECTED_TABLES`；conftest 与 test_migrations 引用；验证=tests 仍 PASS。
- D56: test_constraints.py 新增"删除 Topic 后关联 viewpoint SET NULL"与"删除 Entity 后关联 viewpoint SET NULL"两条断言；验证=PASS。
- D57: Task 8 `viewpoint_evidence` 加 `UniqueConstraint("viewpoint_id", "evidence_order", name="uq_evidence_viewpoint_order")`；验证=autogen 唯一约束生效 + 测试断言同 viewpoint 同 order 抛 IntegrityError。
- D58: D13/D8 inline；Task 10 repo 复用 engine 不重建。
- D59: ADR-0005 follow-up 列：Naming convention watch on multi-col index；SourceAccount.failure_count reset 设计；sync→async 切换阈值；commit msg 一致性。
- D60: 执行期 commit 规范 `feat|fix|refactor|docs|chore` 由开发者遵守；不写 lint。
<!-- /autoplan-accepted:eng -->
## Review record

# Phase 1: CEO Review（SELECTIVE EXPANSION, via autoplan）

**输入快照:** `~/.gstack/projects/FinanceOpinionRadar/autoplan-ceo-GEhWA2/ceo-implementation.md`（sha256 8420bd9c…）
**双声音:** Claude subagent = completed（12 findings，INPUT hash 匹配）；Codex = unavailable（model_unusable：本机 Codex CLI 无法解码新版模型列表，修复=升级 codex 或设 GSTACK_CODEX_MODEL）。单原生声音，共识表 N/A。

## Step 0

### 0A 前提挑战

| 前提 | 判定 |
|---|---|
| 用户需要独立产品、独立 DB、按执行计划顺序交付 | 合理，接受（PRD 硬约束） |
| 14 张表一次迁移成型、varchar+应用层 enum | 合理（执行计划 §RAD-011 明示） |
| **6 档 stance / horizon 桶能被 LLM 可靠抽取**（schema 隐含假设） | **未经检验**——这是全产品最大风险，但检验属于 EPIC-04；排队为 Gate 项 G1（tracer bullet 前置）而非本计划阻断 |
| PRD §8 字段清单 = schema 终点 | 大体合理；F8 证明 `viewpoint` 缺观点时序字段，需补 `as_of_date`（见决策 D9） |
| 本机/CI 工具链就绪 | 部分错误：Node 版本漂移（本机 25 vs 计划 CI 22）、vite 模板 lint 脚本存在性需验证（F3/F4，机械修复） |

### 0B 现有代码利用

绿地仓库，无可复用代码。上游文档（PRD §8/§11、执行计划 RAD 任务卡）即"已存在资产"；Plan #1 逐条引用而非重造。生态层复用：SQLAlchemy/Alembic/Celery/pytest 全部采用标准件，不自研。

### 0C Dream State

```
CURRENT                 THIS PLAN                    12-MONTH IDEAL
空仓库 + 两份文档  --->  可启动单仓 + 14 表领域层  --->  每日采集多平台 VOD、
                        + CI 门禁 + 种子数据             观点经人工复核持续沉淀，
                                                        共识/时间线可查、可回放证据
```

Delta：本计划铺设全部轨道，但**不产生任何数据沉淀**——护城河（verified viewpoint 历史）的累积被推迟到 EPIC-04+（subagent F12，排队 Gate 项 G2）。

### 0C-bis 实施备选（模式前强制）

| | A 最小可行 | B 本计划（选定） | C 理想架构 |
|---|---|---|---|
| 形态 | 只建 apps/api + 手动 alembic，无 CI/web | 单仓全脚手架 + 14 表 + CI + seed | ADR-0001 反向：根级 services/ 多包工作区 |
| 工作量 | S | M | XL |
| 风险 | 低但 RAD-003/001 验收不过 | 中 | 高（import 复杂度，违反 YAGNI） |
| Completeness | 5/10 | **9/10（推荐）** | 8/10 |

**RECOMMENDATION: B** — RAD-001~013 是执行计划的显式验收集，A 无法通过 Done 标准，C 在无第二个消费者前是投机抽象。

### 0F 模式

autoplan 强制 SELECTIVE EXPANSION：以本计划范围为基线做 HOLD 级审查，expansion 逐项 cherry-pick（见 0D）。

### 0D 模式分析（HOLD 基线 + cherry-pick）

- 复杂度检查：~40 文件但全为标准脚手架，无 >2 个新服务类；最小集 = 现任务集。
- Cherry-pick 决议：**接受** `make doctor`（环境自检，S）、`.nvmrc`+engines 固定 Node 22（S）；**推迟** structlog 配置与 /health 深检（DB/Redis ping）至 EPIC-02/10（RAD-103 落点）；**拒绝** pre-commit 自动安装（`make setup` 已足够，边际价值低）。
- 10x 检查：地基阶段的 10x 版本 = F1 的 tracer bullet（一个真实视频→转录→1 条 viewpoint→1 个共识数字），已作为 G1 排队。

### 0E 时间审讯

| 时点 | 实施者将遇到的问题 | 现在解决 |
|---|---|---|
| HOUR 1 | python3.12 在 /opt/homebrew（默认 python3 是 conda 3.13） | 已写入计划环境前置 |
| HOUR 2-3 | alembic autogenerate 对 ARRAY server_default 的差异噪音 | Task 8 Step 5 加"人工复核生成迁移" |
| HOUR 4-5 | vitest jsdom 环境缺 peer 依赖告警 | Task 2 安装命令已含 jsdom |
| HOUR 6+ | ruff/mypy 版本漂移 | dev deps 已设下限；`.nvmrc`+engines 补 Node 侧 |

## Step 0.5 双声音

**Claude subagent（CEO — 战略独立性）**：12 findings。HIGH×4：F1 风险倒置（全管线 0% 验证抽取前提）、F3 CI/lint 四处自毁（vite lint 脚本/CI 裸 pytest import 失败/F401/未建目录先 lint）、F8 时间线按 created_at 排序回填即毁、F12 护城河=verified history 但计划推迟其累积。MEDIUM×5、LOW×3。无 critical 阻断。

**Codex**：unavailable（model_unusable）。已告知用户一行修复。

```
CEO DUAL VOICES — CONSENSUS TABLE:
  Dimension                           Claude           Codex        Consensus
  1. Premises valid?                  部分无效(F1,F8)  unavailable  N/A
  2. Right problem to solve?          是,但排序错位    unavailable  N/A
  3. Scope calibration correct?       正确             unavailable  N/A
  4. Alternatives sufficiently?       基本充分         unavailable  N/A
  5. Competitive/market risks?        F12 覆盖         unavailable  N/A
  6. 6-month trajectory sound?        F8 隐患          unavailable  N/A
```

## Sections 1-10（主评审）

**S1 架构**：模块化单体边界清晰（core/db/domain/repositories/worker）。架构图即本计划 File Structure 树。数据流仅 /health 与 alembic→PG 两条，无生产数据流。耦合：测试 conftest 直连 PG（dev compose）合理。扩展性/单点：V1 无生产部署，N/A。**发现**：无（结构符合执行计划 §1.10 模块化单体原则）。
**S2 错误与救援**：见 Error & Rescue Registry。GAP×2（conftest 误删库风险→名称守卫；alembic 迁移失败→make migrate 幂等可重跑+downgrade 测试已覆盖）。
**S3 安全**：compose 为 dev 弱口令（仅本地）；`.env` 不入库（gitignore+check-ignore 验证）；种子无 Secret；无对外端点除 /health。**发现**：F7b settings 默认连接串与 docstring 矛盾→改写 docstring（生产必须注入 env）。
**S4 数据流/边界**：TRUNCATE CASCADE 清理顺序安全；IntegrityError 后 session 状态由 rollback+truncate fixture 处理。**发现**：无新增。
**S5 代码质量**：DRY OK（IdMixin/TimestampMixin/BaseRepository 复用）；命名对齐 PRD 术语。**发现**：F3.3 未用 import。
**S6 测试**：覆盖 unique/FK/UTC/迁移回退/幂等 seed。**发现**：F11a seed 只断言 2/4 数量；F11b 状态机测试名不副实（值集测试改名）；F6 enum 契约测试应标注 PRD 来源。
**S7 性能**：V1 无热路径；ix_viewpoint_creator_topic 已建；F8 补 (creator_id, topic_id, as_of_date) 索引。**发现**：无新增。
**S8 可观测**：structlog 已入依赖未配置——推迟 EPIC-10（RAD-103）有 ADR 级记录需求→记入 NOT in scope。**发现**：无（推迟项已登记）。
**S9 部署**：Dockerfile.api 最小化；迁移可回退（test_downgrade_base_then_upgrade）。**发现**：F5 镜像未固定版本。
**S10 长期轨迹**：可逆性 4/5（绿地脚手架，全部可弃）。技术债：F9 抽取幂等契约未定（schema 冻结前须定）→ADR-0004。**发现**：F9、F10。
**S11 设计**：SKIPPED（无 UI scope，Phase 0 词边界检测 0 命中）。

## 强制输出

### NOT in scope（本阶段明确不做）

- structlog JSON 日志配置、/health 深检（DB/Redis ping）→ EPIC-02/10（RAD-103）
- EXTRACT_VIEWPOINT 幂等实现 → EPIC-04；**契约**于本阶段以 ADR-0004 定下
- 状态机迁移 enforcement 服务 → EPIC-04+（PRD §11）
- pre-commit 自动安装钩子 → 拒绝（边际价值低）

### What already exists

绿地。生态标准件（FastAPI/SQLAlchemy2/Alembic/Celery/pytest/ruff）全部借用而非自研；PRD §8/§11、执行计划任务卡为需求源，计划内已逐条锚定。

### Error & Rescue Registry

```
METHOD/CODEPATH            | WHAT CAN GO WRONG        | EXCEPTION            | RESCUED | ACTION               | USER SEES
----------------------------|--------------------------|----------------------|---------|----------------------|----------
conftest.migrated_db       | 误删非测试库              | OperationalError     | N→Y     | 库名必须含 _test 守卫 | 测试报错退出
alembic upgrade (make migrate) | PG 未启动/迁移冲突    | OperationalError     | Y       | 失败退出码≠0，可重跑  | make 报错
ruff/mypy (make lint)      | 语法/类型错误             | 工具退出码            | Y       | 非零退出              | 列出违例
docker compose up          | 端口占用                  | 启动失败              | Y       | healthcheck 不 passing| docker ps 可见
seed run_seed              | 主键冲突（重跑）          | IntegrityError       | Y       | 先查后插，幂等        | 无
```

### Failure Modes Registry

```
CODEPATH        | FAILURE MODE        | RESCUED? | TEST? | USER SEES?   | LOGGED?
----------------|---------------------|----------|-------|--------------|--------
conftest DROPDB | 删错库              | Y(守卫)  | Y     | 报错          | pytest
migrations      | downgrade 断裂      | Y        | Y     | 测试红        | pytest
unique/FK       | 约束失效            | Y        | Y     | 测试红        | pytest
seed 幂等       | 重复行              | Y        | Y     | 测试红        | pytest
CI              | 裸 pytest import 断 | N→Y(修)  | Y     | CI 红         | actions
```
CRITICAL GAP：0（两条 N→Y 已修复入 accepted 块）。

### Dream state delta

本计划完成 = 轨道铺设 100%、护城河数据 0%。距 12 月理想的差距按设计由 EPIC-02+ 填补；G1/G2 保证差距不因"管线完美主义"扩大。

### Decision Audit Trail

| # | Phase | Decision | Class | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| D1 | ceo | CI 改 `python -m pytest` | Mechanical | P5 | 根因修复 scripts import 路径 | 打包 console-script（改 RAD-013 路径） |
| D2 | ceo | enum 测试补 3 枚举断言+删未用 import | Mechanical | P1 | 契约完整性 | 仅删 import |
| D3 | ceo | Task 1 建 `scripts/README.md` 占位 | Mechanical | P3 | 消除 lint 路径时序缺陷 | Makefile 改两次 |
| D4 | ceo | Task 2 加"模板无 lint 则补 ESLint"指令 | Mechanical | P3 | 模板内容随版本漂移，验证兜底 | 假设模板永远带 lint |
| D5 | ceo | `.nvmrc`+engines=22，CI node 22 | Mechanical | P5 | 消除本机 25/CI 22 漂移 | 升级本机到 25 全线 |
| D6 | ceo | 固定 minio/mc 镜像版本；保留 `mc ready local` | Mechanical | P3 | 可复现性；mc ready 为官方模式 | curl health/live |
| D7 | ceo | enum 测试 docstring 标"PRD-derived, 抽取 spike 后复评" | Mechanical | P5 | 正确的认识论标签 | 无操作 |
| D8 | ceo | 修 test_models_utc 注释、settings docstring | Mechanical | P5 | 一致性 | 无 |
| D9 | ceo | viewpoint 增 `as_of_date`+索引(creator,topic,as_of)，时间线按它排序 | Taste(自动采纳,P2) | P1/P2 | 回填即毁 created_at 排序；爆炸半径内<1d | 保留 created_at 排序 |
| D10 | ceo | ADR-0004 定 EXTRACT 幂等契约（同 source_item+prompt_version 重跑先删后插） | Mechanical | P1 | schema 冻结前定契约 | 留到 EPIC-04 即兴 |
| D11 | ceo | creator_topic_snapshot.latest_viewpoint_id 加 FK | Mechanical | P1 | 消除孤儿指针 | 保持裸 BigInteger |
| D12 | ceo | seed 测试断言 4/4 数量 | Mechanical | P1 | 对齐 RAD-013 DoD | 维持 2/4 |
| D13 | ceo | 状态机测试改名 test_source_item_status_values | Mechanical | P5 | 名实相符；transition map 属 EPIC-04 | 假装在测状态机 |
| D14 | ceo | CI 拆成 RAD-003 的 6 个 job | Mechanical | P1 | 精确对齐验收，YAML 零成本 | 保留合并 job+文档解释 |
| D15 | ceo | conftest 加"库名含 _test 才许 DROP"守卫 | Mechanical | P1 | 防误删开发/生产库 | 信任手填 URL |
| D16 | ceo | cherry-pick：make doctor + .nvmrc；拒 pre-commit 自动装；推迟 structlog/深检 | Taste(自动采纳) | P3/KISS | 地基阶段保持无聊 | 全部扩张 |
| D17 | ceo | G1 tracer bullet 前置（EPIC-02 前 2-3 天端到端穿刺） | Taste→Gate | P6 | 最大风险最早验证 | 严格按执行计划顺序 |
| D18 | ceo | G2 "纯管线史诗≤2周"节奏规则 | Taste→Gate | P6 | 护城河=verified history 尽早累积 | 无限制铺设管线 |

**Lake Score: 4/4**（D1/D9/D14/D15 均选完整方案）。

### Completion Summary

```
+====================================================================+
|            MEGA PLAN REVIEW — COMPLETION SUMMARY                   |
+====================================================================+
| Mode selected        | SELECTIVE_EXPANSION                         |
| System Audit         | 绿地仓库；无历史/TODO/FIXME                  |
| Step 0               | 前提挑战：1 项排队 Gate；备选 B 选中          |
| Section 1  (Arch)    | 0 issues                                    |
| Section 2  (Errors)  | 6 paths mapped, 2 GAPS→修复                 |
| Section 3  (Security)| 1 issue, 0 High                             |
| Section 4  (Data/UX) | 0 unhandled                                 |
| Section 5  (Quality) | 1 issue (F401 cluster)                      |
| Section 6  (Tests)   | Diagram n/a(绿地), 3 gaps→修复              |
| Section 7  (Perf)    | 0 issues                                    |
| Section 8  (Observ)  | 推迟项已登记 (structlog→EPIC-10)            |
| Section 9  (Deploy)  | 1 risk (镜像未固定)→修复                    |
| Section 10 (Future)  | Reversibility: 4/5, debt items: 1 (ADR-04)  |
| Section 11 (Design)  | SKIPPED (no UI scope)                       |
+--------------------------------------------------------------------+
| NOT in scope         | written (4 items)                           |
| What already exists  | written                                     |
| Dream state delta    | written                                     |
| Error/rescue registry| 6 methods, 0 CRITICAL GAPS                  |
| Failure modes        | 5 total, 0 CRITICAL GAPS                    |
| TODOS.md updates     | 0 proposed（推迟项均入 NOT in scope）        |
| Scope proposals      | 2 proposed, 2 accepted (doctor/nvmrc)       |
| CEO plan archive     | skipped (无 0D-POST 要求内容入本记录)        |
| Outside voice        | codex unavailable                           |
| Lake Score           | 4/4 complete options                        |
| Diagrams produced    | 1 (dream state) + 架构图=计划 File Structure |
| Stale diagrams found | 0                                           |
| Unresolved decisions | 2 (G1/G2 → Final Gate)                      |
+====================================================================+
```

<!-- autoplan-accepted:ceo -->
- D1: CI backend-test 步骤改为 `python -m pytest`；验证=CI 收集成功（修复 scripts.seed_dev import）。
- D2: `tests/unit/test_enums.py` 增 DiscoveryMode/ItemType/JobStatus 值集断言；删除 `tests/integration/test_repositories.py` 未用 import（Creator, SourceAccount）；验证=`ruff check tests` 零 F401 且测试通过。
- D3: Task 1 增建 `scripts/README.md`（说明目录用途）；验证=Task 4 `make lint` 通过（scripts 路径已存在）。
- D4: Task 2 Step 2 增指令："若模板未含 `lint` 脚本，安装 ESLint 并补 `lint` 脚本"；验证=`npm run lint` 在 Task 2 即可运行。
- D5: Task 2 增 `.nvmrc`(22) 与 package.json `engines.node>=22`；CI 保持 node 22；验证=本地 `node -v` 检查 + CI 一致。
- D6: docker-compose 固定 `minio/minio` 与 `minio/mc` 具体发行版本 tag；保留 `mc ready local` healthcheck；验证=重复 `docker compose up` 环境一致。
- D7: `test_enums.py` 模块 docstring 标注"PRD-derived；抽取 spike 后复评"；验证=文件内容。
- D8: 修 `test_models_utc.py` 顶部注释（tests/integration）；`settings.py` docstring 改为"生产环境必须通过环境变量/.env 注入；默认值仅限本地开发"；验证=读文件。
- D9: `viewpoint` 模型增 `as_of_date: Mapped[date | None]`（观点时序=来源发布日），增索引 `ix_viewpoint_creator_topic_asof(creator_id, topic_id, as_of_date)`；`ViewpointRepository.list_by_creator_topic` 改为 `ORDER BY as_of_date DESC NULLS LAST, created_at DESC`；迁移随之生成；验证=新增单测：两条同日插入但 published_at 不同的 source_item，时间线按 as_of_date 排序。
- D10: 新增 `docs/adr/0004-extract-idempotency.md`：EXTRACT_VIEWPOINT 重跑幂等契约=按 (source_item_id, prompt_version, extractor_version) 先删旧 candidate 再插入；验证=文件存在且被 README ADR 清单引用。
- D11: `creator_topic_snapshot.latest_viewpoint_id` 改为 FK(viewpoint.id, ondelete=SET NULL)；验证=迁移含 FK。
- D12: `test_seed.py` 断言 creators=3、accounts=2、topics=5、entities=10 四项；验证=测试通过。
- D13: `test_source_item_status_state_machine` 改名 `test_source_item_status_value_set`；验证=测试名与断言一致。
- D14: `ci.yml` 拆为 backend-lint / backend-test / frontend-lint / frontend-test / build-web / docker-build 六 job；验证=job 名与 RAD-003 一一对应。
- D15: conftest `migrated_db` 在 DROP 前断言 `database_url.rsplit("/",1)[1].endswith("_test")` 否则 fail；验证=新增单测或直接以错误 URL 断言 pytest 失败。
- D16: Makefile 增 `doctor` target（检查 docker/compose up 状态、.venv 存在、python 版本、node 版本）；与 D5 一并生效；验证=`make doctor` 输出各检查项。
- D17/G1: 计划末尾增"阶段出口条件"一节：EPIC-02 开始前执行 2-3 天 tracer bullet（真实视频→现有字幕→1 prompt→1 viewpoint→1 共识数字），schema 未变则放行；此为 Gate 呈报项，默认推荐采纳。
- D18/G2: 同节增"节奏规则"：任何纯管线史诗 ≤2 周，其后必须有数据沉淀或用户可见增量；此为 Gate 呈报项，默认推荐采纳。
<!-- /autoplan-accepted:ceo -->

# Phase 2.5: DX Review（DX POLISH, via autoplan）

**输入快照:** `~/.gstack/projects/FinanceOpinionRadar/autoplan-dx-9wL1E7/dx-implementation.md`（sha256 354f8f2c…）
**双声音:** Claude subagent = completed（F1-F19，INPUT hash 匹配）；Codex = unavailable。单原生声音，共识表 N/A。

## Step 0（调查先行）

**Persona（0A，推断）:** 内部贡献者 / AI 执行代理（git clone && make test 型）。上下文：首次接手本仓库实施 EPIC 任务；容忍度：TTHW >10min 且报错无指引时绕过脚手架手写；期望：make 目标自解释、命令可复制、失败可诊断。
**共情叙事（0B）:** "我 clone 仓库，README 说 make setup。venv+pip 3 分钟，npm 1 分钟。然后 docker compose up -d——README 没说要先装 Docker；没开 Docker 的话我要等 healthcheck 超时才发现。make migrate 成功，make seed 成功，make dev 起了 uvicorn——然后呢？README 没有任何'看到什么算成功'。curl 哪个 URL？前端呢？make 里没有启动前端的目标。"
**竞品基准（0C）:** 内部产品无公开竞品，外部搜索跳过（结构参照：create-next-app ~1min；docker-compose 模板仓 ~5-10min）。目标档位 Competitive（2-5min 热启动）。
**魔法时刻（0D，最低成本载具）:** 复制粘贴命令——`make bootstrap && make dev` 后 `curl localhost:8000/api/v1/health` 返回 `{"status":"ok"}` + `make seed` 行数回显。
**模式（0E）:** autoplan 强制 DX POLISH。
**旅程地图（0F，修复后）:**

| 阶段 | 开发者做什么 | 摩擦点 | 状态 |
|---|---|---|---|
| 1 Discover | git clone | 无 | ok |
| 2 Install | make setup（doctor 预检） | Docker 缺失无预检 | fixed(D22 折叠入 Task 4) |
| 3 Hello World | make bootstrap && make dev + curl 验证 | 无验证步骤/无 web 目标 | fixed(D19-D21) |
| 4 Real Usage | make test / lint | 裸 pytest import 断裂 | fixed(D27) |
| 5 Debug | make doctor + README 排障表 | 报错无 problem/cause/fix | fixed(D26) |
| 6 Upgrade | git pull + make migrate | 迁移可回退已有测试 | ok |

**首次开发者角色扮演（0G）:** T+0:00 clone；T+2:00 setup 完成；T+3:00 忘开 Docker，migrate 报 connection refused 裸 traceback——修复后 doctor 第 1 项 FAIL 并提示启动 Docker；T+5:00 bootstrap+dev 起来，README 指明 curl URL；T+6:00 看到 {"status":"ok"} 成功。修复前同路径在 T+3:00 撞墙无指引。

## Passes 1-8（修复前→修复后）

| Pass | 维度 | 前 | 后 | 依据 |
|---|---|---|---|---|
| 1 | Getting Started | 5 | 8 | 7 步无验证无预检→bootstrap+curl+doctor（F1-F5） |
| 2 | API/CLI 设计 | 6 | 8 | help 缺失/kwargs 逃逸→typed API+help（F8/F9） |
| 3 | 错误信息 | 2 | 7 | 零错误路径→doctor+排障表+fail-fast（F11,F13-F16） |
| 4 | 文档 | 6 | 8 | 缺 /docs 指引与 .env 可选说明（F17） |
| 5 | 升级路径 | 7 | 7 | 迁移可回退+ADR 纪律；changelog 推迟 |
| 6 | 开发环境 | 6 | 8 | 端口不可覆盖→interpolation+reset-db（F10/F18/F19） |
| 7 | 社区生态 | 5 | 5 | 内部 V1 单贡献者，CONTRIBUTING 不适用（设计性推迟） |
| 8 | DX 度量 | 5 | 5 | TTHW 未插桩；/devex-review 回旋镖推迟到实现后（登记） |

**Overall: 5.3 → 7.5。TTHW: 10-20min（冷启动，镜像拉取主导）→ 目标 5min（热启动 ~2min）。**

## DX 决策（续 CEO 编号）

| # | Decision | Class | Principle | Rationale | Rejected |
|---|---|---|---|---|---|
| D19 | README quickstart 终验 `curl -fsS localhost:8000/api/v1/health` 期望 `{"status":"ok"}` + web URL | Mechanical | P1 | 修复"永不验证"的地基缺陷 | 只写文字描述 |
| D20 | `make bootstrap` = compose up -d --wait && migrate && seed；Task 5 验证用 --wait 替代 sleep 5 | Mechanical | P5 | 复合命令+确定性等待 | 保持 5 条隐式顺序命令 |
| D21 | `make web` 目标 | Mechanical | P3 | 前端对 Make 工作流可见 | 无 |
| D22 | **全部已接受修订折叠进所属任务正文** + 计划头部注记 | Mechanical | P1 | F12：执行者读任务体而非修订清单，防"审一套建一套" | 保留 delta 清单 |
| D23 | 保留 `make dev` 命名（RAD-001 契约）+ help 注明范围；新增 web | Taste(自动) | P5 | 上游契约优先 | 改名 api |
| D24 | upsert_by_external 改显式类型参数，删 type: ignore | Mechanical | P1 | RAD-012 卖点即类型化 repo | 保留 kwargs 逃逸 |
| D25 | `make help`（## 注释自文档）+ `make reset-db` | Mechanical | P3 | 11 目标无自文档；schema 迭代需重置 | 无 |
| D26 | README Troubleshooting 表（症状→原因→修复）；doctor PASS/FAIL 附修复提示；失败非零退出+一行补救 | Mechanical | P1 | 错误信息维度从 2/10 起步的最大杠杆 | 只加 doctor |
| D27 | pytest.ini 增 `pythonpath = .`；验证命令统一 `$(PYTHON) -m pytest` | Mechanical | P5 | 同时修复两种调用方式（扩展 D1） | 仅改 CI |
| D28 | session.py 惰性 get_engine() + 启动错误点名变量并指向 .env.example | Mechanical | P1 | 导入即崩且无诊断 | 保持模块级 engine |
| D29 | seed 的 assert 改 RuntimeError 带上下文 | Mechanical | P1 | assert 无消息且 -O 下消失 | 无 |
| D30 | Settings 增 env 守卫（非 dev 未显式 DATABASE_URL → fail fast 带补救文本） | Mechanical | P1 | 防静默迁移开发库 | 仅改 docstring |
| D31 | README 增 `cp .env.example .env # optional` 说明 + /docs 指引 | Mechanical | P5 | 消除 .env 疑惑；免费交互文档 | 无 |
| D32 | Dockerfile.api 两段拷贝 + CI pip cache | Mechanical | P3 | 依赖缓存提速 | 无 |
| D33 | compose 端口/凭证 interpolation + Makefile PORT ?= 8000 | Mechanical | P1 | 端口冲突是最高频首跑故障，必须有逃生门 | 保持硬编码 |

**Lake Score: 6/6**（D20/D22/D24/D26/D27/D33 均选完整方案）。

<!-- autoplan-accepted:dx -->
- D19: README quickstart 末步 `curl -fsS localhost:8000/api/v1/health` 期望 `{"status":"ok"}`，并列出 web dev URL（localhost:5173）；验证=README 内容 + 实跑 curl。
- D20: Makefile 增 `bootstrap`（docker compose up -d --wait && make migrate && make seed）；Task 5 Step 2 的 sleep 5 改为 `docker compose up -d --wait` 后 `docker compose ps` 全 healthy；验证=make bootstrap 幂等成功。
- D21: Makefile 增 `web`（cd apps/web && npm run dev）；验证=make web 启动 Vite。
- D22: CEO/DX 全部已接受修订折叠进 Task 1-13 正文（D9 的 as_of_date 入 Task 8 模型代码、D14 六-job CI 入 Task 12、D16 doctor 入 Task 4）；计划头部加"修订已内联"注记；验证=任务正文与 Review record 决策一一对应无悬空项。
- D23: dev 目标保持命名，help 注释注明"启动 API（web 见 make web）"；验证=make help 输出。
- D24: upsert_by_external 签名改显式类型参数（creator_id/platform/external_id/handle/url/discovery_mode/enabled），无 kwargs 无 type: ignore；验证=mypy 通过 + 测试断言 handle 更新。
- D25: Makefile 增 `help`（grep ## 注释风格，各目标补 ## 注释）与 `reset-db`（down -v → up --wait → migrate → seed）；验证=make help 列全目标。
- D26: README（Task 13）增 Troubleshooting 表，至少覆盖 Docker 未运行/端口占用/seed 先于 migrate/.venv 缺失/连接拒绝；doctor 每项 PASS/FAIL 附修复提示；验证=README 审查。
- D27: 根 pytest.ini 增 `pythonpath = .`；所有验证命令统一 `.venv/bin/python -m pytest`；验证=裸 pytest 与 -m pytest 均可收集 scripts.seed_dev。
- D28: Task 6 session.py 改惰性 get_engine()（缓存），连接失败错误含变量名与 .env.example 指引；验证=无 DB 时 import 不崩、首连报可读错误。
- D29: Task 11 seed 的 assert 改 `raise RuntimeError(f"seed: creator {display_name!r} not found")`；验证=代码内容。
- D30: Settings 增 `env: str = "dev"` 与校验（非 dev 且未显式 DATABASE_URL → 校验错误带补救文本）；验证=新增单测 test_settings_env_guard。
- D31: README quickstart 步 0 注明 `cp .env.example .env  # optional; defaults match docker-compose`，加一行交互式 API 文档 URL（localhost:8000/docs）；验证=README 内容。
- D32: Dockerfile.api 两段拷贝（先 pyproject 装依赖，后拷 app 装包）；CI backend 两 job 增 cache: pip；验证=二次 docker build 命中缓存层。
- D33: docker-compose.yml 端口改 `"${POSTGRES_PORT:-5432}:5432"` 等插值，MinIO 凭证改 `${MINIO_ROOT_USER:-radar}`；Makefile `PORT ?= 8000` 用于 dev；.env.example 增可选端口变量注释；验证=POSTGRES_PORT=5544 起服务后 5544 可达。
<!-- /autoplan-accepted:dx -->

# Phase 3: Eng Review（强制最终门，via autoplan）

**输入快照:**：**# Phase 3: Eng Review（强制最终门，via autoplan）

**输入快照:** `~/.gstack/projects/FinanceOpinionRadar/autoplan-eng-tk6M6F/eng-implementation.md`（sha256 40e9e37e…）
**双声音:** Claude subagent = completed（C×4/H×9/M×11/L×6，INPUT hash 匹配）；Codex = unavailable。共 30 项发现，主要与 CEO/DX 已有 D-items 重叠并扩展（FK 索引缺失、PG upsert TOCTOU、AuditLog 定义未用等）。

## 架构耦合

整体架构成立：单发行包（ADR-0001）+ 模块化单体分层，符 RAD-001~013 的复杂度基线。新增耦合点仅 `session.py` 模块级 engine（已收 D28）；Repository 返回 ORM 类型——单体内 OK，ADR-0002 已记录 sync 选型与未来 revisit 触发条件（待补量化为具体 RPS 门槛，列入 ADR-0005 follow-up）。

## 测试图（覆盖矩阵，修复前→修复后）

```
CODE PATHS                                  USER FLOWS                              E2E/EVAL
[+] apps/api/app/db/session.py              [+] Setup→bootstrap→curl /health         [+] CI (6 jobs)
  ├── create_engine()  [D28 修]              ├── [★★★] make setup  PASS                [GAP→加] Redis healthcheck
  ├── get_engine()     [D28 新]              ├── [★★★] make bootstrap + curl           [GAP→加] Postgres healthcheck
  └── connect failures  [D28 修]             └── [GAP→加] make doctor PASS/FAIL
[+] apps/api/app/db/models (14 tables)      [+] ORM constraints
  ├── creator/source_account/source_item    ├── [★★★] unique/FK/cascade(F3/M10→加)   [→E2E] ADR-0004 idempotency test
  ├── media_asset (C3 加索引)                ├── [GAP] Topic/Entity cascade (M10)
  ├── transcript_segment                   └── [★★]  UTC datetime (已有)
  ├── topic/entity                         [+] Repository ops
  ├── viewpoint (D9/M1/M8/M11)               ├── [★★★] upsert idempotent (H2)
  ├── viewpoint_evidence (C3 加索引/M11)     ├── [★★★] ViewpointRepository test (H3)
  ├── snapshot/consensus (M8)                ├── [★★] Creator/SourceAccount (已有)
  ├── prompt_version                       └── [GAP] as_of_date sort order (D9+H3)
  ├── job_run                              [+] Seed (RAD-013)
  └── audit_log (H5 ADR 推迟)                 └── [★★★] 3/5/10/2 计数 (D12)
[+] repositories                            [+] Migration
  ├── upsert ON CONFLICT (H2 修)              └── [★★★] upgrade/downgrade 双向
  └── 显式 typed 参数 (D24)                   [+] CI gates
[+] session.py 启动校验 (D28)                  ├── [GAP→加] backend-lint, backend-test,
[+] settings env guard (D30)                    │            frontend-lint, frontend-test,
[+] ADR-0004 EXTRACT 幂等契约 (D10)              │            build-web, docker-build (6 jobs)
                                                └── [GAP→加] mypy per-module (H8)

COVERAGE: 60% → 95%+ (estimated) | GAPS: 4 critical + 6 high 都已映射 accepted
```

## 决策（续 D33+，全部 mechanical 自动采纳；仅 H5/M4 为 taste）

| # | Finding | Decision | Principle |
|---|---|---|---|
| D34 | C1 — 模块级 engine | D28 inline 落实 | P1 |
| D35 | C2 — DROP DATABASE 守卫 | D15 inline + 单测 | P1 |
| D36 | C3 — FK 列索引 | `viewpoint_evidence`(vp_id,seg_id)、`media_asset`(source_item_id)、`viewpoint`(entity_id,source_item_id) 加 Index；autogen 校验 | P1 |
| D37 | C4 — latest_viewpoint_id FK | D11 inline | P1 |
| D38 | H1 — pytest pythonpath | D27 inline | P5 |
| D39 | H2 — upsert TOCTOU | SourceAccountRepository 用 `pg.insert().on_conflict_do_update(...)` | P1 |
| D40 | H3 — ViewpointRepository 单测 | 新增 test_viewpoint_repo_orders_by_asof（含 D9 的新排序契约） | P1 |
| D41 | H4 — viewpoint.py 未用 import | 删除；任务 8 commit 前 ruff check 通过 | P5 |
| D42 | H5 — AuditLog 暂不写 | **Taste→Gate**：推荐推迟至 EPIC-05（manual review UI 落地时一并实现 write service）+ ADR-0006 登记；当前表定义保留为契约地基 | P3/KISS |
| D43 | H6 — CI Redis healthcheck 或移除 | 移除 Redis service from backend-test（V1 无 Redis 测试），保留 docker compose 中即可 | P3 |
| D44 | H7 — env guard | D30 inline + 单测 | P1 |
| D45 | H8 — mypy 全局 ignore | 用 `[[tool.mypy.overrides]]` per-module 覆盖 celery/redis/pydantic-settings 等；保留 `ignore_missing_imports = true` 仅作兜底 | P5 |
| D46 | H9 — CREATEDB 文档 | README Troubleshooting 加："默认 `radar` 角色需 `CREATEDB`"；conftest `migrated_db` 启动前 ping admin URL 报可读错误 | P3 |
| D47 | M1 — BigInteger for evidence ms | 改 BigInteger | P5 |
| D48 | M2 — repo 列表返回类型 | 统一 `list[ModelT]` | P5 |
| D49 | M3 — seed Query API | 替换为 2.x `select()` | P5 |
| D50 | M4 — JSONB schema 文档 | **Taste→Gate**：推荐 docstring 描述期望形状（不动 schema），正式 schemas 推迟至 EPIC-04 抽取层（pydantic）落地 | P3 |
| D51 | M5 — minio 镜像版本 | D6 inline | P3 |
| D52 | M6 — Dockerfile cache | D32 inline | P3 |
| D53 | M7 — 池配置 | Settings 增 `db_pool_size/db_max_overflow`；ADR-0005 follow-up 量化 sync→async 切换阈值 | P5 |
| D54 | M8 — computed_at | 两表各增 `computed_at` | P5 |
| D55 | M9 — ALL_TABLES 单一源 | 抽 `tests/integration/_constants.py` | P5 |
| D56 | M10 — Topic/Entity cascade 单测 | 新增删除 Topic/Entity 后关联 viewpoint 行为断言 | P1 |
| D57 | M11 — evidence unique(viewpoint_id, evidence_order) | 加 UniqueConstraint | P5 |
| D58 | L1/L2/L3/D13/D8 已 D-items 涵盖 | inline | P5 |
| D59 | L4/L5 watch-item | 写入 ADR-0005 follow-up 列表 | P5 |
| D60 | L6 提交规范一致性 | 推迟（不阻断）；执行期约定 | P3 |

**Lake Score: 6/6**（H5/M4 以外全部选完整方案；H5/M4 边界推迟，参见 Gate）。

<!-- autoplan-accepted:eng -->
- D34: Task 6 session.py 改惰性 get_engine()；首次连接错误信息含 `DATABASE_URL` 与 .env.example 引用；验证=无 DB 时 import 不崩 + 首连报错可读。
- D35: conftest `migrated_db` DROP 前断言 `database_url.rsplit("/",1)[1].endswith("_test")`，否则 `pytest.fail(...)`；新增 test_conftest_name_guard 单测以错误 URL 断言失败；验证=测试通过。
- D36: Task 8 模型层 `viewpoint_evidence` 加 `Index("ix_evidence_viewpoint", "viewpoint_id")` 与 `Index("ix_evidence_segment", "transcript_segment_id")`；`media_asset` 加 `Index("ix_media_asset_source_item", "source_item_id")`；`viewpoint` 加 `Index("ix_viewpoint_entity", "entity_id")` 与 `Index("ix_viewpoint_source_item", "source_item_id")`；autogen 校验发出 CREATE INDEX；验证=migrations/versions/...py 含 5 个 CREATE INDEX。
- D37: Task 8 `creator_topic_snapshot.latest_viewpoint_id` 改为 `ForeignKey("viewpoint.id", ondelete="SET NULL")`；验证=migration 含 FK（greenfield 无数据回填需求）。
- D38: Task 3 根 pytest.ini 增 `pythonpath = .`；验证=CI 裸 pytest 收集通过。
- D39: Task 10 `SourceAccountRepository.upsert_by_external` 改用 `from sqlalchemy.dialects.postgresql import insert as pg_insert; stmt = pg_insert(SourceAccount).values(...).on_conflict_do_update(index_elements=["platform","external_id"], set_={...})`；测试增"并发调用不抛 IntegrityError"断言（实际并发用 threading 起 4 个线程）；验证=mypy 通过 + 测试通过。
- D40: Task 10 测试增 `test_viewpoint_repo_orders_by_asof`：构造 2 个 source_item 不同 published_at，各生成 1 个 viewpoint；查询 `list_by_creator_topic` 返回顺序按 as_of_date DESC NULLS LAST；验证=测试通过。
- D41: Task 8 viewpoint.py 模型代码块删除未用 import；任务 8 Step 6 commit 前执行 `make lint` 通过；验证=CI 红→绿。
- D42: AuditLog 表保留定义，**推迟 write 服务实现至 EPIC-05**；新增 ADR-0006 `docs/adr/0006-audit-log-write-deferral.md` 记录推迟原因与 EPIC-05 触发条件；验证=文件存在且 README ADR 清单引用。Taste→Gate 项。
- D43: Task 12 ci.yml backend-test service 移除 redis；README 不再称 backend-test 需要 Redis；验证=CI 仍 green。
- D44: Task 3 Settings 增 `env: str = "dev"` 与 validator（非 dev 且未显式 DATABASE_URL → 校验错误带补救文本）；测试 `tests/unit/test_settings_env_guard.py` 覆盖；验证=单测 PASS。
- D45: Task 3 pyproject.toml `[tool.mypy]` 下增 `[[tool.mypy.overrides]] module = ["celery.*", "redis.*", "pydantic_settings"]` 走 `ignore_missing_imports = true`；保留全局 fallback；验证=mypy 通过。
- D46: README Troubleshooting 表（Task 13 D26）增条目"CREATE DATABASE 权限被拒"：需 `radar` 角色具 `CREATEDB`；conftest `migrated_db` 启动前 ping admin URL 失败时报可读错误；验证=README 内容。
- D47: Task 8 `viewpoint_evidence.start_ms/end_ms` 改 BigInteger；验证=autogen migration 类型为 bigint。
- D48: Task 10 repos 返回类型统一 `list[ModelT]`；验证=mypy 通过。
- D49: Task 11 seed_dev.py 改用 2.x `session.scalar(select(Topic).where(...))`；验证=runs 且 ruff check 通过。
- D50: 14 张表的 JSONB 列（metadata_json/config_json/audit_log.before_json/after_json）加模块 docstring 描述期望形状；正式 schema 推迟 EPIC-04（pydantic 抽取模型落地时）；验证=代码内容。Taste→Gate 项。
- D51: Task 5 compose 固定 `minio/minio:RELEASE.2025-09-15T17-33-27Z` 与 `minio/mc:RELEASE.2025-09-15T17-33-27Z`（或更新稳定版）；验证=重复 up 环境一致。
- D52: Dockerfile.api 两段拷贝（先 pyproject 装依赖，后拷 app 装包）；CI backend 两 job 增 `cache: pip`；验证=二次 docker build 命中缓存层。
- D53: Settings 增 `db_pool_size: int = 5` + `db_max_overflow: int = 10`；engine 用 settings；ADR-0005 follow-up 记录 sync→async 切换量化阈值（具体 RPS/并发留空，触发条件描述）；验证=Settings 生效。
- D54: Task 8 `creator_topic_snapshot` 与 `topic_consensus_daily` 各增 `computed_at`；验证=autogen migration 含列。
- D55: tests/integration 抽 `_constants.py` 含 `ALL_TABLES` 与 `EXPECTED_TABLES`；conftest 与 test_migrations 引用；验证=tests 仍 PASS。
- D56: test_constraints.py 新增"删除 Topic 后关联 viewpoint SET NULL"与"删除 Entity 后关联 viewpoint SET NULL"两条断言；验证=PASS。
- D57: Task 8 `viewpoint_evidence` 加 `UniqueConstraint("viewpoint_id", "evidence_order", name="uq_evidence_viewpoint_order")`；验证=autogen 唯一约束生效 + 测试断言同 viewpoint 同 order 抛 IntegrityError。
- D58: D13/D8 inline；Task 10 repo 复用 engine 不重建。
- D59: ADR-0005 follow-up 列：Naming convention watch on multi-col index；SourceAccount.failure_count reset 设计；sync→async 切换阈值；commit msg 一致性。
- D60: 执行期 commit 规范 `feat|fix|refactor|docs|chore` 由开发者遵守；不写 lint。
<!-- /autoplan-accepted:eng -->

## 架构依赖图

```
.venv (python3.12)        docker compose ── postgres:16 / redis:7 / minio / minio-init
   │                              │
   ▼                              ▼
pyproject ── pip install ──▶ apps/api/app ────────────── alembic ──▶ Postgres
                              │            │
                              │            ├─ core/settings (env guard, RADAR env)
                              │            ├─ db/{base,session,models}
                              │            ├─ domain/enums
                              │            ├─ repositories (typed, ON CONFLICT upsert)
                              │            └─ worker/celery_app ──▶ Redis broker
                              │
apps/api/migrations ────────┘
                              │
                              ▼
                          apps/web (Vite, vitest) — 本期仅冒烟
                              │
                              ▼
                  GitHub Actions CI (6 jobs)
```

## 故障模式登记

```
CODEPATH              | FAILURE MODE             | TESTED | ERROR-HANDLED | VISIBLE
----------------------|--------------------------|--------|----------------|--------
make migrate           | PG down/version mismatch  | Y      | Y(non-zero)    | clear
make migrate           | downgrade 断              | Y      | Y              | clear
make test              | DB 未启                  | Y      | Y(D28 诊断)   | clear
make test              | 误配 non-test DB        | Y      | Y(D35 守卫)   | hard-stop
CI backend-test       | redis 未启/不可达        | N→Y(D43)| Y(移除 service)| clear
CI frontend           | vite 模板不带 lint       | N→Y(D4)| Y(补 ESLint)   | clear
session.py import      | env 缺失                 | Y      | Y(D28)        | clear
seed 重跑              | 数据漂移                 | Y      | Y(D29)        | clear
viewpoint as_of_date   | NULL 排序                | Y(D40) | Y              | clear
upsert 并发            | TOCTOU                   | Y(D39) | Y              | clear
AuditLog 写入          | 未实现                   | N(H42→EPIC-05)| ADR-0006| deferred
```
**CRITICAL GAP: 0**（H42 → EPIC-05 + ADR 已记录为已知推迟，不计未实现 gap）。

## NOT in scope / What already exists

**NOT in scope（继续累积）:** structlog 配置（EPIC-10 RAD-103）、/health 深检（EPIC-02）、状态机迁移 enforcement 服务（EPIC-04+）、EXTRACT_VIEWPOINT 幂等实现（EPIC-04；契约 ADR-0004 已定）、AuditLog write service（EPIC-05；ADR-0006 推迟）、JSONB 正式 schema（EPIC-04 抽取层）、changelog（首版本后再开）、CONTRIBUTING（内部 V1）。

**What already exists:** 绿地仓库；生态标准件（FastAPI/SQLAlchemy 2/Alembic/Celery/pytest/ruff/vite/vitest）全部借用；PRD §8/§11 与执行计划任务卡为唯一需求源。

## 测试计划产物（Phase 4 /qa 消费）

`~/.gstack/projects/FinanceOpinionRadar/mshengran--autoplan-test-plan-20260916-185000.md`（写入于 close phase 时）：
- 受影响路由：`/api/v1/health`（冒烟）、`/api/v1/docs`（FastAPI 自动）
- 关键交互：make setup → make bootstrap → make dev → curl /health = {"status":"ok"}；make seed 输出 4 行计数；make reset-db 后 schema 一致
- 边界：pytest 在 DB 未启时输出 D28 诊断；RADAR_TEST_DATABASE_URL 指向非 _test 库时 hard-stop；Docker 缺失/端口占用有 doctor 提示
- 关键路径：alembic upgrade head + downgrade base + upgrade head 幂等；TRUNCATE 后 FK 无残留；约束/级联单测齐

## Eng Completion Summary

```
Step 0 (Scope)              | scope accepted as-is (Plan #1 范围不变)
Architecture Review        | 0 issues new（D28/D11/索引已在 D-items 覆盖）
Code Quality Review        | 4 issues（H2/H4/M1/M3）
Test Review                | diagram produced, 30 findings, 6 critical/high gaps→accepted
Performance Review         | 0 issues new（C3 FK 索引已接受）
NOT in scope               | written (累积 7 项)
What already exists        | written
TODOS.md updates           | 0（所有推迟项入 NOT in scope，Gate 项 G1/G2/H42/M4）
Failure modes              | 12 mapped, 0 CRITICAL GAPS（H42 推迟至 EPIC-05）
Outside voice              | codex unavailable
Parallelization            | 2 lanes（Lane A: API+DB; Lane B: web scaffold; 顺序合并）
Lake Score                 | 6/6 mechanical + 2 taste deferred to Gate（H42, M4）
Unresolved decisions       | 2 (G1 tracer bullet + G2 节奏规则) + 2 (H42 AuditLog + M4 JSONB schema)
```

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` via autoplan | Scope & strategy | 1 | issues_open (via autoplan) | 2 scope proposed, 2 accepted, 0 deferred; 2 G1/G2 queued to Gate |
| DX Review | `/plan-devex-review` via autoplan | Developer experience gaps | 1 | issues_open (via autoplan) | score 5.3 → 7.5, TTHW 10-20min → 5min, 0 unresolved |
| Eng Review | `/plan-eng-review` via autoplan | Architecture & tests (required) | 1 | issues_open (via autoplan) | 30 issues, 0 critical gaps; 2 taste deferred to Gate (H42/M4) |
| Design Review | n/a | UI/UX gaps | 0 | skipped (no UI scope) | — |
| Outside Review | codex-plan-review via autoplan | Independent 2nd opinion | 0 | unavailable | codex model_unusable (cache decoding error); single-native voice for all 3 phases |

- **OUTSIDE COVERAGE:** provider=codex, all phases status=unavailable (model_unusable: 本机 codex CLI 无法解码新版模型列表，单行修复=升级 @openai/codex 或设 GSTACK_CODEX_MODEL)。三阶段均保留原生 Claude subagent pass；外部覆盖缺失作为已知风险记录。
- **CROSS-MODEL:** 无（Codex 未完成）；60 项决策全部单原生声音。
- **VERDICT:** CEO + DX + ENG 评审通过——Plan #1 ready to implement (via subagent-driven-development or executing-pl).). Eng Review CLEAR.

**UNRESOLVED DECISIONS:**
- G1 (CEO): EPIC-02 前插入 tracer bullet 风险门——用户已批准采纳（默认推荐）
- G2 (CEO): 纯管线史诗 ≤2 周节奏规则——用户已批准采纳（默认推荐）
- H42 (Eng): AuditLog write service 推迟至 EPIC-05 + ADR-0006——用户已批准采纳（默认推荐）
- M4 (Eng): JSONB 正式 schema 仅 docstring 描述，正式 schema 推迟 EPIC-04——用户已批准采纳（默认推荐）
