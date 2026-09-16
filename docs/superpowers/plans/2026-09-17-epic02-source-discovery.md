<!-- /autoplan restore point: "/Users/mshengran/.gstack/projects/FinanceOpinionRadar/main-autoplan-restore-20260917-001821.md" -->
## Implementation plan
# EPIC-02 来源发现与媒体解析 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **本仓库既有指令（覆盖推荐）：** 用户要求不用 subagent，主会话逐任务串行执行（Plan #1 同款）。

**Goal:** 打通"内容进入系统"的第一段管道：定义媒体来源 Adapter 契约，用受控 yt-dlp 子进程实现通用适配器，提供手工 URL 解析 API，并落地 Celery 定时发现任务（RAD-020~023）。

**Architecture:** 三层——纯数据契约（`app/services/media/contracts.py`，无 ORM 依赖）→ 子进程适配器（`app/services/media/adapters/yt_dlp.py`，subprocess 调 yt-dlp CLI，超时/白名单/stderr 捕获）→ 编排层（`app/services/discovery.py` + FastAPI 端点 + Celery 任务）。幂等靠既有唯一键 `(source_account_id, external_item_id)` 与 `(platform, external_id)` 的 upsert。

**Tech Stack:** 既有栈不变（FastAPI/SQLAlchemy sync/Celery/pytest）。yt-dlp 以**外部二进制**调用（不进 Python 依赖），测试全部用假二进制 fixture，CI 不需要网络。

---

## 关键决策（先读）

| # | 决策 | 理由 |
|---|---|---|
| C1 | **Adapter 契约为同步 `Protocol`**（执行计划 RAD-020 伪码写了 `async def`，此处偏离） | PRD 5.3 的契约本身就是同步；ADR-0002 已定 sync SQLAlchemy + sync FastAPI 端点；yt-dlp 子进程调用天然阻塞，async 只能靠 to_thread 伪装。真异步切换留到 ADR-0005 的量化阈值。记入 ADR-0007。 |
| C2 | **yt-dlp 用子进程 CLI，不用 Python 库** | RAD-021 明示 subprocess 可选且要求超时/stderr 捕获：`subprocess.run(timeout=)` 能整体杀进程，Python 库内嵌 API 崩溃会拖垮 API 进程；版本由 Docker 镜像钉住。测试注入假二进制路径，无需网络。记入 ADR-0007。 |
| C3 | **契约不 import ORM 模型**；discover 收 `AccountRef`、subtitle/download 收 `ItemRef`（小型 frozen dataclass） | 契约层保持纯数据（PRD 5.3"核心域禁止平台 SDK 细节"的对称面：核心域也不该反向耦合）；测试可直接构造。服务层负责 ORM→Ref 映射。 |
| C4 | **RAD-022 的"确认后创建 source_item"落为两个端点**：`POST /api/v1/source-items/resolve-url`（预览，不写库）+ `POST /api/v1/source-items`（写库） | 预览/创建分离，前端确认流对应两次调用；创建时服务端重新 resolve（管理员工具，双倍解析可接受），避免信任客户端回传的元数据。 |
| C5 | **手工创建的账号/creator 自动 get-or-create**：channel_name→creator，channel_id→source_account(discovery_mode="manual")；无 channel 信息时回退单视频账号 | source_account.creator_id NOT NULL，必须有落点；channel 即"人物"符合 PRD 语义。 |
| C6 | **新增 `dispatch_due_discoveries` beat 任务**（执行计划未明写） | 表里已有 poll_interval_sec 字段，没人调度则 RAD-023 的 discover 永不触发；每 5 分钟扫描 due 账号派发，是最小闭环。 |
| C7 | `prepare_source_item`（EPIC-03 任务）用 `celery_app.send_task("prepare_source_item", ...)` 按名投递 | 目标任务尚未定义，按名投递是 Celery 标准做法，EPIC-03 落地后无缝衔接。 |
| C8 | Plan #1 审查遗留的 3 个 Minor（seed 动态计数、env.py `%` 转义、get_db 错误路径测试）作为 Task 1 清偿 | 审查时明确挂账到 EPIC-02。 |

## File Structure（全部落在 apps/api 发行包内，PRD `services/media/*` → `app/services/media/*`，同 ADR-0001）

```
新建：
apps/api/app/services/__init__.py
apps/api/app/services/media/__init__.py
apps/api/app/services/media/contracts.py        # C1/C3：数据类 + Protocol + 错误层级
apps/api/app/services/media/url_guard.py        # URL scheme/主机白名单校验
apps/api/app/services/media/adapters/__init__.py
apps/api/app/services/media/adapters/yt_dlp.py  # C2：子进程封装 + GenericYtDlpAdapter
apps/api/app/services/discovery.py              # 编排：预览/创建/账号发现
apps/api/app/services/media/factory.py          # get_media_adapter 工厂（F1：api/worker 共用，互不依赖）
apps/api/app/api/__init__.py                    # 空（包标记）
apps/api/app/api/v1/__init__.py                 # api_router
apps/api/app/api/v1/source_items.py             # RAD-022 两个端点
apps/api/app/repositories/source_items.py       # SourceItemRepository.upsert_by_external
apps/api/app/worker/tasks.py                    # RAD-023 Celery 任务
apps/api/migrations/util.py                     # C8：alembic % 转义助手（可测）
tests/fixtures/media/fake_ytdlp.py              # 参数化假二进制（env 驱动行为）
修改：
apps/api/app/core/settings.py                   # 新配置项
apps/api/app/main.py                            # include api_router
apps/api/app/worker/celery_app.py               # beat_schedule
scripts/seed_dev.py                             # C8 动态计数
apps/api/migrations/env.py                      # C8 % 转义
infra/docker/Dockerfile.api                     # 镜像装 yt-dlp
.env.example / README.md / docs/adr/0007*.md
```

测试命令统一从仓库根执行：`.venv/bin/python -m pytest <path> -v`（下文简写 `pytest`）。集成测试需 `make bootstrap` 的 compose 栈在跑（radar_test 库由 conftest 自建）。

---

### Task 1: 清偿 Plan #1 审查遗留（3 个 Minor）

**Files:**
- Modify: `scripts/seed_dev.py`（末行 print 动态计数）
- Create: `apps/api/migrations/util.py` + Modify `apps/api/migrations/env.py:21`
- Test: `tests/unit/test_migrations_util.py`、`tests/unit/test_db_session.py`

- [x] **Step 1.1 seed 动态计数**：`scripts/seed_dev.py` 末行 `print("seed done: creators=3 ...")` 硬编码数字。改为 commit 后实际计数：

```python
        session.commit()
        # 动态计数：数据源常量变更时 print 不会撒谎（Plan #1 审查遗留）
        print(
            "seed done: "
            f"creators={session.query(Creator).count()} "
            f"topics={session.query(Topic).count()} "
            f"entities={session.query(Entity).count()} "
            f"source_accounts={session.query(SourceAccount).count()}"
        )
```

（import 处补 `from app.db.models import Creator, Entity, SourceAccount, Topic`）

- [x] **Step 1.2 写失败测试：alembic % 转义**

```python
# tests/unit/test_migrations_util.py
from migrations.util import alembic_escape


def test_percent_in_password_is_doubled() -> None:
    # alembic Config 用 % 插值：原始口令中的 % 必须翻倍才能存活 set/get_main_option
    assert alembic_escape("postgresql://u:p%40ss@h/db") == "postgresql://u:p%%40ss@h/db"


def test_no_percent_unchanged() -> None:
    assert alembic_escape("postgresql://u:pw@h/db") == "postgresql://u:pw@h/db"
```

运行：`.venv/bin/python -m pytest tests/unit/test_migrations_util.py -v` → FAIL（ModuleNotFoundError: migrations.util）
（migrations 目录非包、也无 `__init__.py` —— 需先 `touch apps/api/migrations/__init__.py` 使 `migrations.util` 可导入。确认 alembic.ini 的 `script_location` 不受影响。）

- [x] **Step 1.3 实现**

```python
# apps/api/migrations/util.py
"""Alembic 配件的可测小工具。"""


def alembic_escape(url: str) -> str:
    """Config.set_main_option 走 %-插值：口令含 % 时必须翻倍，否则 alembic 抛 ValueError。"""
    return url.replace("%", "%%")
```

`env.py:21` 改为：

```python
    config.set_main_option("sqlalchemy.url", alembic_escape(get_settings().database_url))
```

（env.py 顶部加 `from migrations.util import alembic_escape`）。跑 1.2 → PASS。

- [x] **Step 1.4 写失败测试：get_db 错误转译**

```python
# tests/unit/test_db_session.py
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

import app.db.session as session_mod
from app.db.session import get_db


def test_get_db_translates_operational_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = MagicMock()
    monkeypatch.setattr(session_mod, "get_session_factory", lambda: (lambda: fake_session))
    gen = get_db()
    next(gen)
    with pytest.raises(RuntimeError, match="数据库连接失败"):
        gen.throw(OperationalError("stmt", {}, Exception("boom")))


def test_get_db_closes_session(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_session = MagicMock()
    monkeypatch.setattr(session_mod, "get_session_factory", lambda: (lambda: fake_session))
    gen = get_db()
    next(gen)
    gen.close()
    fake_session.close.assert_called_once()
```

运行 → PASS（纯行为回归，实现已存在；若失败说明转译逻辑有缺陷则修 session.py）。

- [x] **Step 1.5 验证 + 提交**

```bash
.venv/bin/python -m pytest tests/unit -v && make lint
git add -A && git commit -m "chore: 清偿 Plan #1 审查遗留（seed 动态计数/env % 转义/get_db 测试）"
```

---

### Task 2: Adapter 契约 contracts.py（RAD-020）

**Files:**
- Create: `apps/api/app/services/__init__.py`、`apps/api/app/services/media/__init__.py`（空）
- Create: `apps/api/app/services/media/contracts.py`
- Test: `tests/unit/test_media_contracts.py`

- [x] **Step 2.1 写失败测试**

```python
# tests/unit/test_media_contracts.py
from datetime import UTC, datetime

from app.services.media.contracts import (
    AccountRef,
    AdapterError,
    DiscoveredItem,
    DownloadResult,
    ItemRef,
    MediaSourceAdapter,
    ResolvedMedia,
    SubtitleTrack,
)


def test_discovered_item_defaults() -> None:
    item = DiscoveredItem(
        external_item_id="v1", title="t", url="https://x/v1",
        published_at=None, duration_ms=None, metadata={},
    )
    assert item.metadata == {}


def test_resolved_media_holds_subtitle_tracks() -> None:
    media = ResolvedMedia(
        platform="youtube", external_item_id="v1", title="t",
        canonical_url="https://x/v1", thumbnail_url=None, duration_ms=1000,
        item_type="vod", published_at=datetime(2026, 1, 1, tzinfo=UTC),
        channel_external_id="ch1", channel_name="频道",
        subtitles=(SubtitleTrack(language="zh", is_auto=False),),
        metadata={},
    )
    assert media.subtitles[0].language == "zh"


def test_generic_adapter_satisfies_protocol() -> None:
    # 结构化 Protocol：任何实现四方法的对象都能赋给契约类型
    class _Fake:
        def discover(self, account: AccountRef) -> list[DiscoveredItem]: return []
        def resolve(self, url: str) -> ResolvedMedia: ...  # type: ignore[empty-body]
        def fetch_subtitle(self, item: ItemRef, language: str | None = None): return None
        def download_media(self, item: ItemRef) -> DownloadResult: ...  # type: ignore[empty-body]

    adapter: MediaSourceAdapter = _Fake()  # 静态满足即通过（mypy 在 CI 兜底）
    assert adapter is not None


def test_adapter_error_hierarchy() -> None:
    from app.services.media.contracts import (
        AdapterProcessError, AdapterTimeoutError, UrlNotAllowedError,
    )
    assert issubclass(UrlNotAllowedError, AdapterError)
    assert issubclass(AdapterProcessError, AdapterError)
    assert issubclass(AdapterTimeoutError, AdapterError)
```

- [x] **Step 2.2 跑测试确认失败**（ModuleNotFoundError）

- [x] **Step 2.3 实现**

```python
# apps/api/app/services/media/contracts.py
"""媒体来源 Adapter 契约（RAD-020）。

同步 Protocol（C1，ADR-0007）：与 sync SQLAlchemy 栈一致，yt-dlp 子进程天然阻塞。
本模块只含纯数据类型与抽象，禁止 import ORM/平台 SDK（C3）。
fetch_subtitle/download 的返回结构在 EPIC-03 充实字段，此处先钉住形状。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class AccountRef:
    """discover 的入参投影：只暴露 Adapter 需要的账号字段。"""
    platform: str
    external_id: str
    url: str | None
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ItemRef:
    """subtitle/download 的入参投影。"""
    external_item_id: str
    canonical_url: str


@dataclass(frozen=True)
class DiscoveredItem:
    """discover 产出的单个内容条目（对应 source_item 的一行候选）。"""
    external_item_id: str
    title: str | None
    url: str
    published_at: datetime | None
    duration_ms: int | None
    metadata: dict


@dataclass(frozen=True)
class SubtitleTrack:
    language: str
    is_auto: bool


@dataclass(frozen=True)
class ResolvedMedia:
    """resolve 的统一输出（RAD-022 API 响应即由它映射）。"""
    platform: str
    external_item_id: str
    title: str | None
    canonical_url: str
    thumbnail_url: str | None
    duration_ms: int | None
    item_type: str  # vod | live
    published_at: datetime | None
    channel_external_id: str | None
    channel_name: str | None
    subtitles: tuple[SubtitleTrack, ...]
    metadata: dict
    channel_url: str | None = None  # E3：频道页 URL（账号 upsert 用），默认 None 兼容测试构造


@dataclass(frozen=True)
class SubtitleResult:
    """EPIC-03 落地真实字段，先钉形状。"""
    language: str
    content: bytes


@dataclass(frozen=True)
class DownloadResult:
    """EPIC-03 落地真实字段，先钉形状。"""
    local_path: str
    size_bytes: int


class AdapterError(Exception):
    """Adapter 层错误基类：API/任务层按子类映射状态。"""


class UrlNotAllowedError(AdapterError):
    """URL 未通过 scheme/主机白名单（R G-021 安全要求）。"""


class AdapterProcessError(AdapterError):
    """外部进程失败（非零退出/输出不可解析），message 携带 stderr 尾部。"""


class AdapterTimeoutError(AdapterError):
    """外部进程超时被杀。"""


class MediaSourceAdapter(Protocol):
    def discover(self, account: AccountRef) -> list[DiscoveredItem]: ...
    def resolve(self, url: str) -> ResolvedMedia: ...
    def fetch_subtitle(self, item: ItemRef, language: str | None = None) -> SubtitleResult | None: ...
    def download_media(self, item: ItemRef) -> DownloadResult: ...
```

- [x] **Step 2.4 跑测试通过；mypy：`.venv/bin/python -m mypy --config-file apps/api/pyproject.toml apps/api/app`**

- [x] **Step 2.5 提交** `git commit -m "feat: 定义媒体来源 Adapter 契约（RAD-020）"`

---

### Task 3: URL 白名单校验 url_guard.py

**Files:**
- Create: `apps/api/app/services/media/url_guard.py`
- Test: `tests/unit/test_media_url_guard.py`

- [x] **Step 3.1 写失败测试**

```python
# tests/unit/test_media_url_guard.py
import pytest

from app.services.media.url_guard import ensure_allowed_url

ALLOW = ("youtube.com", "youtu.be", "bilibili.com", "douyin.com")


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=x",
    "http://youtu.be/x",
    "https://m.bilibili.com/video/BV1xx",
    "https://www.douyin.com/video/1",
])
def test_allowed_hosts_pass(url: str) -> None:
    ensure_allowed_url(url, ALLOW)  # 不抛即通过


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",                    # 协议限制
    "ftp://youtube.com/x",
    "https://evil.com/watch?v=x",            # 主机不在白名单
    "https://youtube.com.evil.com/x",        # 后缀伪造
    "https://user:pass@youtube.com/x",       # userinfo 不可信
    "not-a-url",                             # 无 scheme
    "",                                      # 空串
])
def test_rejected(url: str) -> None:
    from app.services.media.contracts import UrlNotAllowedError
    with pytest.raises(UrlNotAllowedError):
        ensure_allowed_url(url, ALLOW)


def test_error_message_names_url_and_allowlist() -> None:
    from app.services.media.contracts import UrlNotAllowedError
    with pytest.raises(UrlNotAllowedError, match="evil.com"):
        ensure_allowed_url("https://evil.com/x", ALLOW)
```

- [x] **Step 3.2 确认失败 → 实现**

```python
# apps/api/app/services/media/url_guard.py
"""URL 安全闸：Adapter 派生子进程前的第一道校验（RAD-021）。"""

from urllib.parse import urlsplit

from app.services.media.contracts import UrlNotAllowedError

_ALLOWED_SCHEMES = ("http", "https")


def ensure_allowed_url(raw_url: str, allowlist: tuple[str, ...]) -> None:
    """校验 scheme∈{http,https}、主机命中白名单（自身或子域）、无 userinfo。

    不合法即抛 UrlNotAllowedError——在派生子进程之前拒绝，杜绝
    file://、内网地址、凭据注入等进入 yt-dlp 参数。
    """
    try:
        parts = urlsplit(raw_url)
    except ValueError as exc:
        raise UrlNotAllowedError(f"URL 无法解析: {raw_url!r}") from exc
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in _ALLOWED_SCHEMES or not host:
        raise UrlNotAllowedError(f"URL 协议非法或主机为空: {raw_url!r} (允许: {_ALLOWED_SCHEMES})")
    if parts.username is not None or parts.password is not None:
        raise UrlNotAllowedError(f"URL 不允许携带 userinfo: {raw_url!r}")
    if not any(host == d or host.endswith(f".{d}") for d in allowlist):
        raise UrlNotAllowedError(
            f"主机 {host} 不在白名单 {allowlist}，如需放行请配置 MEDIA_HOST_ALLOWLIST"
        )
```

- [x] **Step 3.3 测试通过 → 提交** `git commit -m "feat: 媒体 URL scheme/主机白名单校验"`

---

### Task 4: Settings 扩展

**Files:**
- Modify: `apps/api/app/core/settings.py`
- Test: `tests/unit/test_settings_media.py`

- [x] **Step 4.1 写失败测试**

```python
# tests/unit/test_settings_media.py
from app.core.settings import Settings


def test_media_defaults() -> None:
    s = Settings(env="dev", database_url="postgresql+psycopg://u:p@h/db")
    assert s.ytdlp_binary == "yt-dlp"
    assert s.ytdlp_timeout_sec == 60
    assert s.media_host_allowlist == ("youtube.com", "youtu.be", "bilibili.com", "douyin.com")
    assert s.discover_playlist_max_items == 50
    assert s.discover_dispatch_interval_sec == 300


def test_allowlist_parses_csv_and_strips() -> None:
    s = Settings(
        env="dev", database_url="postgresql+psycopg://u:p@h/db",
        media_host_allowlist=" YouTube.COM , bilibili.com ",
    )
    assert s.media_host_allowlist == ("youtube.com", "bilibili.com")


def test_empty_allowlist_entry_dropped() -> None:
    s = Settings(
        env="dev", database_url="postgresql+psycopg://u:p@h/db",
        media_host_allowlist="youtube.com,,",
    )
    assert s.media_host_allowlist == ("youtube.com",)
```

- [x] **Step 4.2 确认失败 → 实现**（settings.py 增加字段 + validator）

```python
    # --- EPIC-02 媒体发现 ---
    ytdlp_binary: str = "yt-dlp"
    ytdlp_timeout_sec: int = 60
    discover_playlist_max_items: int = 50
    discover_dispatch_interval_sec: int = 300
```

（E6 定稿——唯一实现：字段声明 tuple 默认值，`field_validator(mode="before")` 把环境变量 CSV 解析为 tuple，env 名自动映射 `MEDIA_HOST_ALLOWLIST`：

```python
    media_host_allowlist: tuple[str, ...] = (
        "youtube.com", "youtu.be", "bilibili.com", "douyin.com",
    )

    @field_validator("media_host_allowlist", mode="before")
    @classmethod
    def _parse_allowlist(cls, v: object) -> object:
        # 环境变量是 CSV（MEDIA_HOST_ALLOWLIST=youtube.com,bilibili.com）
        if isinstance(v, str):
            return tuple(h.strip().lower() for h in v.split(",") if h.strip())
        return v
```

env 名自动映射 `MEDIA_HOST_ALLOWLIST`。）

- [x] **Step 4.3 测试通过 → 提交** `git commit -m "feat: 媒体发现配置项（ytdlp 二进制/超时/白名单/调度）"`

`.env.example` 追加（Task 11 一并做，此处只动 settings+tests）。

---

### Task 5: 假二进制 fixture + yt-dlp 子进程封装

**Files:**
- Create: `tests/fixtures/media/fake_ytdlp.py`（chmod +x）、`tests/fixtures/__init__.py`（空，如缺）
- Create: `apps/api/app/services/media/adapters/__init__.py`（空）
- Create: `apps/api/app/services/media/adapters/yt_dlp.py`（本任务只做 `_run_ytdlp` 进程封装）
- Test: `tests/unit/test_ytdlp_process.py`

- [x] **Step 5.1 假二进制**（env 驱动行为，覆盖 RAD-021 全部 fixture 场景）

```python
#!/usr/bin/env python3
"""假 yt-dlp：FAKE_YTDLP_BEHAVIOR ∈ success|private|badurl|timeout|badjson。

success 时从 FAKE_YTDLP_PAYLOAD 读 JSON 原样打到 stdout。
"""
import json
import os
import sys
import time

behavior = os.environ.get("FAKE_YTDLP_BEHAVIOR", "success")

if behavior == "timeout":
    time.sleep(60)
    sys.exit(0)
if behavior in ("private", "badurl"):
    msg = (
        "ERROR: [private] This video is private."
        if behavior == "private"
        else "ERROR: Unsupported URL: some garbage"
    )
    print(msg, file=sys.stderr)
    sys.exit(1)
if behavior == "badjson":
    print("this is not json")
    sys.exit(0)

payload = json.loads(os.environ.get("FAKE_YTDLP_PAYLOAD", "{}"))
print(json.dumps(payload, ensure_ascii=False))
```

`chmod +x tests/fixtures/media/fake_ytdlp.py`（git 保留执行位）。

- [x] **Step 5.2 写失败测试**（子进程封装层：超时/stderr/无 shell）

```python
# tests/unit/test_ytdlp_process.py
import json
from pathlib import Path

import pytest

from app.services.media.adapters.yt_dlp import YtDlpProcess
from app.services.media.contracts import AdapterProcessError, AdapterTimeoutError

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "media" / "fake_ytdlp.py")


def run(monkeypatch: pytest.MonkeyPatch, behavior: str, payload: dict | None = None, timeout: int = 10):
    # E1：统一 monkeypatch（与 Task 6/7 同款），失败也自动还原，杜绝 env 串扰
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", behavior)
    if payload is not None:
        monkeypatch.setenv("FAKE_YTDLP_PAYLOAD", json.dumps(payload))
    return YtDlpProcess(binary=FAKE, timeout_sec=timeout).run_json(
        ["--dump-single-json", "https://www.youtube.com/watch?v=x"]
    )


def test_run_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(monkeypatch, "success", {"id": "abc"}) == {"id": "abc"}


def test_nonzero_exit_raises_with_stderr_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterProcessError, match="private"):
        run(monkeypatch, "private")


def test_unsupported_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterProcessError, match="Unsupported URL"):
        run(monkeypatch, "badurl")


def test_timeout_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterTimeoutError):
        run(monkeypatch, "timeout", timeout=1)


def test_unparseable_stdout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterProcessError, match="无法解析"):
        run(monkeypatch, "badjson")


def test_missing_binary_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # GAP-1：二进制缺失 → AdapterProcessError 且消息含 YTDLP_BINARY 指引
    proc = YtDlpProcess(binary="/nonexistent/yt-dlp", timeout_sec=5)
    with pytest.raises(AdapterProcessError, match="YTDLP_BINARY"):
        proc.run_json(["--dump-single-json", "https://www.youtube.com/watch?v=x"])
```

（E1：helper 已统一 monkeypatch；方法名统一 `run_json`。）

- [x] **Step 5.3 实现**

```python
# apps/api/app/services/media/adapters/yt_dlp.py
"""yt-dlp CLI 子进程封装与通用适配器（RAD-021，C2/ADR-0007）。"""

import json
import subprocess

from app.services.media.contracts import (
    AdapterError,
    AdapterProcessError,
    AdapterTimeoutError,
)

_STDERR_TAIL_CHARS = 500


class YtDlpProcess:
    """受控 yt-dlp 子进程：禁 shell、限时、捕获 stderr、stdout 只接受 JSON。"""

    def __init__(self, binary: str = "yt-dlp", timeout_sec: int = 60) -> None:
        self._binary = binary
        self._timeout_sec = timeout_sec

    def run_json(self, args: list[str]) -> dict:
        argv = [self._binary, *args]
        try:
            proc = subprocess.run(  # noqa: S603  argv 列表直传，无 shell 拼接
                argv,
                capture_output=True,
                text=True,
                timeout=self._timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterTimeoutError(f"yt-dlp 超时（>{self._timeout_sec}s）: {args[0]}") from exc
        except FileNotFoundError as exc:
            raise AdapterProcessError(f"yt-dlp 二进制不存在: {self._binary!r}，检查 YTDLP_BINARY") from exc
        if proc.returncode != 0:
            tail = proc.stderr.strip()[-_STDERR_TAIL_CHARS:]
            raise AdapterProcessError(f"yt-dlp 退出码 {proc.returncode}: {tail or '(无 stderr)'}")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AdapterProcessError(
                f"yt-dlp stdout 无法解析为 JSON: {proc.stdout[:_STDERR_TAIL_CHARS]!r}"
            ) from exc
```

（测试里的方法名同步为 `run_json`。）

- [x] **Step 5.4 测试通过 → 提交** `git commit -m "feat: 受控 yt-dlp 子进程封装（超时/stderr/JSON 校验）"`

---

### Task 6: GenericYtDlpAdapter.resolve

**Files:**
- Modify: `apps/api/app/services/media/adapters/yt_dlp.py`（追加 adapter 类 + 解析函数）
- Test: `tests/unit/test_ytdlp_adapter_resolve.py`

- [x] **Step 6.1 写失败测试**（RAD-021 五类 fixture 全覆盖）

```python
# tests/unit/test_ytdlp_adapter_resolve.py
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import UrlNotAllowedError

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "media" / "fake_ytdlp.py")
URL = "https://www.youtube.com/watch?v=abc123"

# E4：此常量落 tests/fixtures/media/payloads.py，供 unit/integration 共同 import
SUCCESS_PAYLOAD = {
    "id": "abc123",
    "title": "美联储加息点评",
    "extractor_key": "Youtube",
    "webpage_url": URL,
    "thumbnail": "https://i.ytimg.com/vi/abc123/hq.jpg",
    "duration": 1250.5,
    "is_live": False,
    "upload_date": "20260315",
    "channel_id": "ch_42",
    "channel": "宏观日记",
    "subtitles": {"zh-Hans": [{"ext": "vtt"}]},
    "automatic_captions": {"en": [{"ext": "vtt"}]},
}


def make_adapter(monkeypatch: pytest.MonkeyPatch, behavior: str, payload: dict | None = None):
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", behavior)
    if payload is not None:
        monkeypatch.setenv("FAKE_YTDLP_PAYLOAD", json.dumps(payload, ensure_ascii=False))
    return GenericYtDlpAdapter(
        binary=FAKE, timeout_sec=5,
        allowlist=("youtube.com", "youtu.be"),
    )


def test_resolve_success_maps_all_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    media = make_adapter(monkeypatch, "success", SUCCESS_PAYLOAD).resolve(URL)
    assert media.platform == "youtube"
    assert media.external_item_id == "abc123"
    assert media.title == "美联储加息点评"
    assert media.duration_ms == 1250500
    assert media.item_type == "vod"
    assert media.published_at == datetime(2026, 3, 15, tzinfo=UTC)
    assert media.channel_external_id == "ch_42"
    assert media.channel_name == "宏观日记"
    assert media.thumbnail_url is not None
    langs = [(t.language, t.is_auto) for t in media.subtitles]
    assert ("zh-Hans", False) in langs and ("en", True) in langs


def test_resolve_no_subtitles(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {k: v for k, v in SUCCESS_PAYLOAD.items() if k not in ("subtitles", "automatic_captions")}
    media = make_adapter(monkeypatch, "success", payload).resolve(URL)
    assert media.subtitles == ()


def test_resolve_live_item(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {**SUCCESS_PAYLOAD, "is_live": True}
    media = make_adapter(monkeypatch, "success", payload).resolve(URL)
    assert media.item_type == "live"


def test_resolve_missing_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"id": "x", "extractor_key": "Youtube", "webpage_url": URL}
    media = make_adapter(monkeypatch, "success", payload).resolve(URL)
    assert media.title is None and media.duration_ms is None
    assert media.published_at is None and media.channel_external_id is None
    assert media.item_type == "vod"


def test_resolve_rejects_url_before_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = make_adapter(monkeypatch, "success", SUCCESS_PAYLOAD)
    with pytest.raises(UrlNotAllowedError):
        adapter.resolve("https://evil.com/watch?v=x")


def test_resolve_private_video_wraps_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.media.contracts import AdapterProcessError
    with pytest.raises(AdapterProcessError, match="private"):
        make_adapter(monkeypatch, "private").resolve(URL)


def test_resolve_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.media.contracts import AdapterTimeoutError
    adapter = GenericYtDlpAdapter(binary=FAKE, timeout_sec=1, allowlist=("youtube.com",))
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", "timeout")
    with pytest.raises(AdapterTimeoutError):
        adapter.resolve(URL)
```

- [x] **Step 6.2 确认失败 → 实现**（yt_dlp.py 追加）

```python
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.services.media.contracts import (
    AccountRef,
    DiscoveredItem,
    ItemRef,
    MediaSourceAdapter,
    ResolvedMedia,
    SubtitleResult,
    SubtitleTrack,
    UrlNotAllowedError,
    ensure_allowed_url,  # 见下：re-export 自 url_guard（循环 import 注意：url_guard 只 import contracts，安全）
)


class GenericYtDlpAdapter:
    """通用 yt-dlp 适配器：一个实现覆盖 YouTube/B 站/抖音等全部 yt-dlp 支持的平台。"""

    def __init__(
        self,
        *,
        binary: str = "yt-dlp",
        timeout_sec: int = 60,
        allowlist: tuple[str, ...] = ("youtube.com", "youtu.be", "bilibili.com", "douyin.com"),
        playlist_max_items: int = 50,
    ) -> None:
        self._proc = YtDlpProcess(binary=binary, timeout_sec=timeout_sec)
        self._allowlist = allowlist
        self._playlist_max_items = playlist_max_items

    # --- EPIC-02 实现 ---

    def resolve(self, url: str) -> ResolvedMedia:
        ensure_allowed_url(url, self._allowlist)
        data = self._proc.run_json(
            ["--dump-single-json", "--no-playlist", "--no-warnings", "--skip-download", url]
        )
        return _parse_resolved(data)

    def discover(self, account: AccountRef) -> list[DiscoveredItem]:
        if not account.url:
            raise AdapterError(f"账号 {account.external_id!r} 无 URL，无法 discover")
        ensure_allowed_url(account.url, self._allowlist)
        data = self._proc.run_json(
            [
                "--dump-single-json", "--flat-playlist", "--no-warnings",
                "--playlist-items", f"1:{self._playlist_max_items}", account.url,
            ]
        )
        entries = data.get("entries") or []
        return [_parse_entry(e) for e in entries if e.get("id")]

    # --- EPIC-03 接口占位（契约完整性优先，实现随 ASR 落地） ---

    def fetch_subtitle(self, item: ItemRef, language: str | None = None) -> SubtitleResult | None:
        raise NotImplementedError("EPIC-03 RAD-030")

    def download_media(self, item: ItemRef) -> DownloadResult:
        raise NotImplementedError("EPIC-03 RAD-031")


def _parse_resolved(data: dict) -> ResolvedMedia:
    from urllib.parse import urlsplit  # 如顶层已 import 则不重复

    platform = str(data.get("extractor_key", "generic")).lower()
    duration = data.get("duration")
    return ResolvedMedia(
        platform=platform,
        external_item_id=str(data.get("id", "")),
        title=data.get("title"),
        canonical_url=data.get("webpage_url") or "",
        thumbnail_url=data.get("thumbnail"),
        duration_ms=int(duration * 1000) if duration is not None else None,
        item_type="live" if data.get("is_live") else "vod",
        published_at=_parse_published_at(data),
        channel_external_id=data.get("channel_id") or data.get("uploader_id"),
        channel_name=data.get("channel") or data.get("uploader"),
        subtitles=_parse_subtitles(data),
        metadata={k: data[k] for k in ("view_count", "like_count", "language", "description") if k in data},
        channel_url=data.get("channel_url"),  # E3
    )


def _parse_published_at(data: dict) -> datetime | None:
    ts = data.get("timestamp") or data.get("release_timestamp")
    if ts:
        return datetime.fromtimestamp(int(ts), tz=UTC)
    raw = data.get("upload_date")  # yt-dlp: "YYYYMMDD"
    if raw and len(str(raw)) == 8 and str(raw).isdigit():
        d = str(raw)
        return datetime(int(d[:4]), int(d[4:6]), int(d[6:8]), tzinfo=UTC)
    return None


def _parse_subtitles(data: dict) -> tuple[SubtitleTrack, ...]:
    tracks: list[SubtitleTrack] = []
    for lang, fmts in (data.get("subtitles") or {}).items():
        if fmts:
            tracks.append(SubtitleTrack(language=lang, is_auto=False))
    for lang, fmts in (data.get("automatic_captions") or {}).items():
        if fmts:
            tracks.append(SubtitleTrack(language=lang, is_auto=True))
    return tuple(tracks)
```

注意：`ensure_allowed_url` 从 `url_guard` import 而非定义在 contracts —— Task 3 的 url_guard 已提供。`_parse_entry` 在 Task 7 一并补（本任务 discover 会先缺，mypy 报错 → 本任务先给最小 `_parse_entry` 占位并 raise NotImplementedError? **不**：把 discover 整体留到 Task 7，本任务只加 resolve + _parse_* —— 类方法 discover 同步追加，避免中间态编译错误。）

- [x] **Step 6.2b 补充边界测试（Eng GAP-2）**：`_parse_published_at` 的 timestamp 分支（upload_date 分支已被 SUCCESS_PAYLOAD 覆盖）：

```python
def test_resolve_published_at_from_unix_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {**SUCCESS_PAYLOAD, "timestamp": 1773792000, "upload_date": None}
    media = make_adapter(monkeypatch, "success", payload).resolve(URL)
    assert media.published_at == datetime.fromtimestamp(1773792000, tz=UTC)
```

- [x] **Step 6.3 测试通过 → 提交** `git commit -m "feat: GenericYtDlpAdapter.resolve 统一元数据解析（RAD-021）"`

---

### Task 7: GenericYtDlpAdapter.discover

**Files:**
- Modify: `apps/api/app/services/media/adapters/yt_dlp.py`（补 discover + _parse_entry）
- Test: `tests/unit/test_ytdlp_adapter_discover.py`

- [x] **Step 7.1 写失败测试**

```python
# tests/unit/test_ytdlp_adapter_discover.py
import json
from pathlib import Path

import pytest

from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import AccountRef, AdapterError

FAKE = str(Path(__file__).resolve().parents[1] / "fixtures" / "media" / "fake_ytdlp.py")
ACCOUNT = AccountRef(
    platform="youtube", external_id="ch_42",
    url="https://www.youtube.com/@macro-diary/videos",
)

PLAYLIST = {
    "id": "ch_42",
    "entries": [
        {"id": "v1", "title": "视频一", "url": "https://www.youtube.com/watch?v=v1", "duration": 600},
        {"id": "v2", "title": None, "url": "https://www.youtube.com/watch?v=v2"},
        # 无 id 的坏条目应被跳过
        {"title": "broken"},
    ],
}


def make(monkeypatch: pytest.MonkeyPatch, behavior: str, payload: dict | None = None):
    monkeypatch.setenv("FAKE_YTDLP_BEHAVIOR", behavior)
    if payload is not None:
        monkeypatch.setenv("FAKE_YTDLP_PAYLOAD", json.dumps(payload, ensure_ascii=False))
    return GenericYtDlpAdapter(binary=FAKE, timeout_sec=5, allowlist=("youtube.com",), playlist_max_items=2)


def test_discover_maps_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    items = make(monkeypatch, "success", PLAYLIST).discover(ACCOUNT)
    assert [i.external_item_id for i in items] == ["v1", "v2"]
    assert items[0].duration_ms == 600000
    assert items[0].url.endswith("v1")
    assert items[0].published_at is None  # flat-playlist 无日期，EPIC-03 resolve 时补


def test_discover_empty_playlist(monkeypatch: pytest.MonkeyPatch) -> None:
    items = make(monkeypatch, "success", {"entries": []}).discover(ACCOUNT)
    assert items == []


def test_discover_account_without_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AdapterError, match="无 URL"):
        make(monkeypatch, "success", PLAYLIST).discover(
            AccountRef(platform="youtube", external_id="ch_42", url=None)
        )


def test_discover_rejects_bad_account_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.media.contracts import UrlNotAllowedError
    bad = AccountRef(platform="youtube", external_id="x", url="https://evil.com/videos")
    with pytest.raises(UrlNotAllowedError):
        make(monkeypatch, "success", PLAYLIST).discover(bad)
```

- [x] **Step 7.2 确认失败 → 实现**

```python
def _parse_entry(entry: dict) -> DiscoveredItem:
    duration = entry.get("duration")
    return DiscoveredItem(
        external_item_id=str(entry["id"]),
        title=entry.get("title"),
        url=entry.get("url") or entry.get("webpage_url") or "",
        published_at=None,  # flat-playlist 条目无日期；prepare_source_item（EPIC-03）resolve 时回填
        duration_ms=int(duration * 1000) if duration is not None else None,
        metadata={k: entry[k] for k in ("live_status", "view_count") if k in entry},
    )
```

- [x] **Step 7.3 测试通过 → 提交** `git commit -m "feat: GenericYtDlpAdapter.discover 频道条目发现（RAD-021）"`

---

### Task 8: SourceItemRepository.upsert_by_external

**Files:**
- Create: `apps/api/app/repositories/source_items.py`
- Modify: `apps/api/app/repositories/__init__.py`（如有导出列表）
- Test: `tests/integration/test_source_item_repo.py`

- [x] **Step 8.1 写失败测试**

```python
# tests/integration/test_source_item_repo.py
from sqlalchemy.orm import Session

from app.db.models import Creator, SourceAccount
from app.repositories.source_items import SourceItemRepository


def _account(session: Session) -> SourceAccount:
    creator = Creator(display_name="测试作者", status="active")
    session.add(creator)
    session.flush()
    account = SourceAccount(
        creator_id=creator.id, platform="youtube", external_id="ch1",
        discovery_mode="manual",
    )
    session.add(account)
    session.flush()
    return account


def test_upsert_creates_then_updates(db_session: Session) -> None:
    repo = SourceItemRepository(db_session)
    account = _account(db_session)

    created = repo.upsert_by_external(
        source_account_id=account.id, external_item_id="v1",
        title="初版标题", canonical_url="https://www.youtube.com/watch?v=v1",
    )
    assert created.id is not None

    # 二次 upsert：同 (account, external) 不产生新行，字段刷新
    updated = repo.upsert_by_external(
        source_account_id=account.id, external_item_id="v1",
        title="新标题", canonical_url="https://www.youtube.com/watch?v=v1",
        duration_ms=123000,
    )
    db_session.flush()
    assert updated.id == created.id
    assert updated.title == "新标题"
    assert updated.duration_ms == 123000
    assert repo.count() == 1


def test_upsert_returns_created_flag(db_session: Session) -> None:
    """服务层需要区分新建（要投递 prepare）与已存在（不重复投递）。"""
    repo = SourceItemRepository(db_session)
    account = _account(db_session)

    item, is_new = repo.upsert_by_external(..., return_created=True)  # 形状以实现为准
    assert is_new is True
    _, is_new2 = repo.upsert_by_external(..., return_created=True)
    assert is_new2 is False
```

（第二测试的调用形状由实现定稿——若选择返回 `(item, created)` 二元组，统一两处调用。）

- [x] **Step 8.2 确认失败 → 实现**

```python
# apps/api/app/repositories/source_items.py
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import SourceItem
from app.repositories.base import BaseRepository


class SourceItemRepository(BaseRepository[SourceItem]):
    model = SourceItem

    def upsert_by_external(
        self,
        *,
        source_account_id: int,
        external_item_id: str,
        title: str | None = None,
        description: str | None = None,
        canonical_url: str | None = None,
        thumbnail_url: str | None = None,
        published_at=None,
        duration_ms: int | None = None,
        item_type: str = "vod",
        metadata_json: dict | None = None,
    ) -> tuple[SourceItem, bool]:
        """按 (source_account_id, external_item_id) 幂等 upsert，返回 (行, 是否新建)。

        新建标志供 discover 流程决定是否投递 prepare_source_item（C7）。
        """
        stmt = pg_insert(SourceItem).values(
            source_account_id=source_account_id,
            external_item_id=external_item_id,
            title=title,
            description=description,
            canonical_url=canonical_url,
            thumbnail_url=thumbnail_url,
            published_at=published_at,
            duration_ms=duration_ms,
            item_type=item_type,
            status="discovered",
            metadata_json=metadata_json or {},
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["source_account_id", "external_item_id"],
            set_={
                "title": stmt.excluded.title,
                "duration_ms": stmt.excluded.duration_ms,
                "thumbnail_url": stmt.excluded.thumbnail_url,
                "canonical_url": stmt.excluded.canonical_url,
            },
        ).returning(SourceItem.__table__)  # 或执行后回查；以“先 xmax 判新建”或“回查”最简实现为准
        ...
```

（定稿实现采用最直白方案：`INSERT ... ON CONFLICT DO UPDATE` 后 `SELECT` 回查行；新建判定用 `xmax = 0` 系统列或先 SELECT 后 INSERT 的 PG 原子写法。**最简正确**：先 SELECT，命中即更新返回 `(row, False)`；未命中走 ON CONFLICT DO UPDATE（兜并发）后回查返回 `(row, True)`。并发窗口极小且有唯一键兜底，语义清晰。）

- [x] **Step 8.3 跑集成测试（需 compose 栈）：`.venv/bin/python -m pytest tests/integration/test_source_item_repo.py -v`**

- [x] **Step 8.4 提交** `git commit -m "feat: SourceItemRepository 幂等 upsert（含新建标志）"`

---

### Task 9: 发现编排服务 discovery.py

**Files:**
- Create: `apps/api/app/services/discovery.py`
- Test: `tests/integration/test_discovery_service.py`

- [x] **Step 9.1 写失败测试**（用 Stub Adapter，不打网络）

```python
# tests/integration/test_discovery_service.py
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import SourceAccount
from app.services.discovery import create_item_from_url, discover_account, resolve_url_preview
from tests.fixtures.media.payloads import SUCCESS_PAYLOAD  # E4：共享载荷模块，避免跨测试模块导入


class StubAdapter:
    """够用的桩：resolve/discover 返回固定值，记录调用。"""

    def __init__(self, resolved=None, discovered=None):
        self._resolved = resolved
        self._discovered = discovered
        self.resolve_calls: list[str] = []
        self.discover_calls: list[str] = []

    def resolve(self, url: str):
        self.resolve_calls.append(url)
        if isinstance(self._resolved, Exception):
            raise self._resolved
        return self._resolved

    def discover(self, account):
        self.discover_calls.append(account.external_id)
        if isinstance(self._discovered, Exception):
            raise self._discovered
        return self._discovered


def _resolved(**over):
    from app.services.media.contracts import ResolvedMedia, SubtitleTrack
    base = dict(
        platform="youtube", external_item_id="abc123", title="美联储加息点评",
        canonical_url="https://www.youtube.com/watch?v=abc123",
        thumbnail_url=None, duration_ms=1250500, item_type="vod",
        published_at=datetime(2026, 3, 15, tzinfo=UTC),
        channel_external_id="ch_42", channel_name="宏观日记",
        subtitles=(SubtitleTrack("zh-Hans", False),), metadata={},
    )
    base.update(over)
    return ResolvedMedia(**base)


def test_create_item_from_url_creates_creator_account_item(db_session: Session) -> None:
    adapter = StubAdapter(resolved=_resolved())
    item = create_item_from_url("https://www.youtube.com/watch?v=abc123", db_session, adapter)

    assert item.external_item_id == "abc123"
    assert item.source_account_id is not None
    account = db_session.get(SourceAccount, item.source_account_id)
    assert account.platform == "youtube" and account.external_id == "ch_42"
    assert account.discovery_mode == "manual"
    assert account.creator.display_name == "宏观日记"
    assert item.status == "discovered"


def test_create_item_from_url_is_idempotent(db_session: Session) -> None:
    adapter = StubAdapter(resolved=_resolved())
    first = create_item_from_url("https://www.youtube.com/watch?v=abc123", db_session, adapter)
    second = create_item_from_url("https://www.youtube.com/watch?v=abc123", db_session, adapter)
    assert first.id == second.id
    assert db_session.query(SourceAccount).count() == 1


def test_create_item_without_channel_falls_back_to_item_account(db_session: Session) -> None:
    adapter = StubAdapter(resolved=_resolved(channel_external_id=None, channel_name=None))
    item = create_item_from_url("https://www.youtube.com/watch?v=abc123", db_session, adapter)
    account = db_session.get(SourceAccount, item.source_account_id)
    # C5 回退：账号键 = 视频自身 id，creator 名 = "未知来源"
    assert account.external_id == "abc123"


def test_resolve_url_preview_does_not_write(db_session: Session) -> None:
    from app.db.models import SourceItem
    adapter = StubAdapter(resolved=_resolved())
    media = resolve_url_preview("https://www.youtube.com/watch?v=abc123", db_session, adapter)
    assert media.external_item_id == "abc123"
    assert db_session.query(SourceItem).count() == 0


def test_discover_account_upserts_and_marks_success(db_session: Session) -> None:
    # 造一个 auto_poll 账号
    creator = ...  # 同 Task 8 辅助
    account = SourceAccount(creator_id=creator.id, platform="youtube",
                            external_id="ch_42", url="https://www.youtube.com/@x/videos",
                            discovery_mode="auto_poll")
    db_session.add(account); db_session.flush()

    from app.services.media.contracts import DiscoveredItem
    items = [DiscoveredItem("v1", "一", "https://www.youtube.com/watch?v=v1", None, None, {}),
             DiscoveredItem("v2", "二", "https://www.youtube.com/watch?v=v2", None, None, {})]
    sent: list[tuple] = []
    adapter = StubAdapter(discovered=items)
    outcome = discover_account(account.id, db_session, adapter, send=lambda name, **kw: sent.append((name, kw)))

    assert outcome["discovered"] == 2 and outcome["created"] == 2
    assert account.last_success_at is not None
    assert account.failure_count == 0
    assert [kw["args"] for _, kw in sent] == [[item_id] for item_id in (1, 2)]  # prepare_source_item(item_id)
    assert sent[0][0] == "prepare_source_item"

    # 再跑一次：无新建、不重复投递（幂等）
    outcome2 = discover_account(account.id, db_session, adapter, send=lambda name, **kw: sent.append((name, kw)))
    assert outcome2["created"] == 0
    assert len(sent) == 2


def test_discover_account_missing_idempotent_on_existing_items(db_session: Session) -> None:
    # 同一 external_item_id 已存在于别的账号 → 不应串号（唯一键按账号隔离）
    ...


def test_discover_account_failure_increments_counter(db_session: Session) -> None:
    ...  # 账号 setup 同上
    adapter = StubAdapter(discovered=RuntimeError("yt-dlp 崩了"))
    with pytest.raises(RuntimeError):
        discover_account(account.id, db_session, adapter, send=lambda *a, **k: None)
    db_session.rollback()
    refreshed = db_session.get(SourceAccount, account.id)
    assert refreshed.failure_count == 1
    assert refreshed.last_success_at is None
```

（`send` 参数注入投递函数，任务层传 `celery_app.send_task`，测试传收集 lambda——C7 的可测缝隙。）

- [x] **Step 9.2 确认失败 → 实现**

```python
# apps/api/app/services/discovery.py
"""来源发现编排（RAD-022/023）：resolve → 建/联账号 → upsert 条目 → 派生下游任务。"""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models import Creator, SourceAccount, SourceItem
from app.repositories.creators import CreatorRepository
from app.repositories.source_accounts import SourceAccountRepository
from app.repositories.source_items import SourceItemRepository
from app.services.media.contracts import (
    AdapterError,
    DiscoveredItem,
    MediaSourceAdapter,
    ResolvedMedia,
)

# send 的默认实现留给 worker 装配；服务层只依赖Callable，便于测试（C7）
SendFunc = Callable[..., object]

FALLBACK_CREATOR_NAME = "未知来源"


def resolve_url_preview(url: str, session: Session, adapter: MediaSourceAdapter) -> ResolvedMedia:
    """RAD-022 预览：只解析不落库。"""
    return adapter.resolve(url)


def create_item_from_url(url: str, session: Session, adapter: MediaSourceAdapter) -> SourceItem:
    """RAD-022 确认后创建：服务端重新 resolve（C4），账号/creator get-or-create（C5）。"""
    media = adapter.resolve(url)
    creators = CreatorRepository(session)
    creator = creators.get_by_name(media.channel_name or FALLBACK_CREATOR_NAME)
    if creator is None:
        creator = creators.create(display_name=media.channel_name or FALLBACK_CREATOR_NAME)

    accounts = SourceAccountRepository(session)
    # channel 级账号；无 channel 信息回退为"每视频一账号"
    account_external_id = media.channel_external_id or media.external_item_id
    account = accounts.upsert_by_external(
        creator_id=creator.id,
        platform=media.platform,
        external_id=account_external_id,
        url=media.channel_url,  # E3：显式字段（metadata 快照里从未放过 channel_url）
        discovery_mode="manual",
    )
    item, _created = SourceItemRepository(session).upsert_by_external(
        source_account_id=account.id,
        external_item_id=media.external_item_id,
        title=media.title,
        canonical_url=media.canonical_url,
        thumbnail_url=media.thumbnail_url,
        published_at=media.published_at,
        duration_ms=media.duration_ms,
        item_type=media.item_type,
        metadata_json={"resolved": True, **{t.language: t.is_auto for t in media.subtitles}},
    )
    session.commit()
    return item


def discover_account(
    account_id: int,
    session: Session,
    adapter: MediaSourceAdapter,
    *,
    send: SendFunc,
) -> dict:
    """RAD-023：六步——load → discover → upsert → 新条目派发 → 记成功 → 失败计数。"""
    account = session.get(SourceAccount, account_id)
    if account is None or not account.enabled:
        return {"account_id": account_id, "skipped": True, "discovered": 0, "created": 0}

    from app.services.media.contracts import AccountRef
    items = adapter.discover(AccountRef(
        platform=account.platform, external_id=account.external_id,
        url=account.url, config=account.config_json,
    ))
    repo = SourceItemRepository(session)
    created_ids: list[int] = []
    for discovered in items:
        item, is_new = repo.upsert_by_external(
            source_account_id=account.id,
            external_item_id=discovered.external_item_id,
            title=discovered.title,
            canonical_url=discovered.url,
            duration_ms=discovered.duration_ms,
            metadata_json={"discovered_via": "discover_job"},
        )
        if is_new:
            created_ids.append(item.id)
    for item_id in created_ids:
        send("prepare_source_item", args=[item_id])  # C7：按名投递，EPIC-03 落地实现
    account.last_success_at = datetime.now(UTC)
    account.failure_count = 0
    session.commit()
    logger.info("discover_ok", account_id=account_id, discovered=len(items), created=len(created_ids))
    return {
        "account_id": account_id, "skipped": False,
        "discovered": len(items), "created": len(created_ids),
    }
```

失败计数语义（与测试对齐，F2）：任何异常令 `failure_count += 1` 后**重抛**（Celery 标记任务失败）。
捕获点是编排边界 `except Exception`——任务边界的合法模式：记完整上下文日志后重抛，绝不吞错。
Celery 自动重试明确不用（会双计 failure_count）：

```python
    import structlog
    logger = structlog.get_logger(__name__)
    try:
        items = adapter.discover(...)
        ...（upsert/派发/成功标记/commit）
    except Exception:
        session.rollback()
        account.failure_count += 1
        # F6：结构化日志——排障时能从日志重建现场
        logger.exception("discover_failed", account_id=account_id, platform=account.platform)
        session.commit()
        raise
```

- [x] **Step 9.3 集成测试通过 → 提交** `git commit -m "feat: 来源发现编排服务（预览/手工创建/账号发现）"`

---

### Task 10: 手工 URL 解析 API（RAD-022）

**Files:**
- Create: `apps/api/app/api/__init__.py`（`get_media_adapter` 依赖工厂）、`apps/api/app/api/v1/__init__.py`（api_router）、`apps/api/app/api/v1/source_items.py`
- Modify: `apps/api/app/main.py`（`app.include_router(api_router)`）
- Test: `tests/unit/test_api_source_items.py`

- [x] **Step 10.1 写失败测试**

```python
# tests/unit/test_api_source_items.py
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.media.contracts import AdapterProcessError, ResolvedMedia, SubtitleTrack


class StubAdapter:
    def __init__(self, resolved=None):
        self._resolved = resolved
    def resolve(self, url: str):
        if isinstance(self._resolved, Exception):
            raise self._resolved
        return self._resolved
    def discover(self, account): raise AssertionError("API 层不应触发 discover")
    def fetch_subtitle(self, item, language=None): raise NotImplementedError
    def download_media(self, item): raise NotImplementedError


def _resolved(**over):
    base = dict(
        platform="youtube", external_item_id="abc123", title="美联储加息点评",
        canonical_url="https://www.youtube.com/watch?v=abc123", thumbnail_url="https://t/hq.jpg",
        duration_ms=1250500, item_type="vod",
        published_at=datetime(2026, 3, 15, tzinfo=UTC),
        channel_external_id="ch_42", channel_name="宏观日记",
        subtitles=(SubtitleTrack("zh-Hans", False), SubtitleTrack("en", True)), metadata={},
    )
    base.update(over)
    return ResolvedMedia(**base)


@pytest.fixture
def client():
    return TestClient(app)


def _install(monkeypatch, adapter):
    from app.services.media.factory import get_media_adapter
    # 端点用 Depends(get_media_adapter) → 走 dependency_overrides：
    app.dependency_overrides[get_media_adapter] = lambda: adapter
    yield
    app.dependency_overrides.pop(get_media_adapter, None)


def test_resolve_url_returns_metadata(client, monkeypatch):
    with _install(monkeypatch, StubAdapter(_resolved())):
        resp = client.post("/api/v1/source-items/resolve-url", json={"url": "https://www.youtube.com/watch?v=abc123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform"] == "youtube"
    assert body["external_id"] == "abc123"
    assert body["duration_ms"] == 1250500
    assert body["thumbnail_url"] == "https://t/hq.jpg"
    assert body["subtitle_languages"] == ["zh-Hans", "en"]


def test_resolve_url_invalid_body_422(client):
    resp = client.post("/api/v1/source-items/resolve-url", json={"url": "not-a-url"})
    assert resp.status_code == 422


def test_resolve_url_not_allowed_400(client, monkeypatch):
    from app.services.media.contracts import UrlNotAllowedError
    with _install(monkeypatch, StubAdapter(UrlNotAllowedError("主机 evil.com 不在白名单"))):
        resp = client.post("/api/v1/source-items/resolve-url", json={"url": "https://evil.com/x"})
    assert resp.status_code == 400
    assert "白名单" in resp.json()["detail"]


def test_resolve_url_upstream_failure_502(client, monkeypatch):
    with _install(monkeypatch, StubAdapter(AdapterProcessError("yt-dlp 退出码 1: private"))):
        resp = client.post("/api/v1/source-items/resolve-url", json={"url": "https://www.youtube.com/watch?v=x"})
    assert resp.status_code == 502


def test_resolve_url_timeout_504(client, monkeypatch):  # GAP-3
    with _install(monkeypatch, StubAdapter(AdapterTimeoutError("yt-dlp 超时（>60s）"))):
        resp = client.post("/api/v1/source-items/resolve-url", json={"url": "https://www.youtube.com/watch?v=x"})
    assert resp.status_code == 504
```

（`POST /source-items`（写库）的端到端在集成侧补一个薄用例 `tests/integration/test_api_create_source_item.py`：StubAdapter + 真库 → 201 → 幂等再调 200。）

- [x] **Step 10.2 确认失败 → 实现**

```python
# apps/api/app/services/media/factory.py  （F1：独立于 api/worker，二者共用）
from functools import lru_cache

from app.core.settings import get_settings
from app.services.media.adapters.yt_dlp import GenericYtDlpAdapter
from app.services.media.contracts import MediaSourceAdapter


@lru_cache
def get_media_adapter() -> MediaSourceAdapter:
    s = get_settings()
    return GenericYtDlpAdapter(
        binary=s.ytdlp_binary, timeout_sec=s.ytdlp_timeout_sec,
        allowlist=s.media_host_allowlist, playlist_max_items=s.discover_playlist_max_items,
    )
```

```python
# apps/api/app/api/v1/__init__.py
from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")

from app.api.v1 import source_items  # noqa: E402

api_router.include_router(source_items.router)
```

```python
# apps/api/app/api/v1/source_items.py
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, HttpUrl
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.media.factory import get_media_adapter
from app.services import discovery
from app.services.media.contracts import (
    AdapterProcessError, AdapterTimeoutError, MediaSourceAdapter, UrlNotAllowedError,
)

router = APIRouter(prefix="/source-items", tags=["source-items"])


class ResolveUrlRequest(BaseModel):
    url: HttpUrl


class ResolveUrlResponse(BaseModel):
    platform: str
    external_id: str
    title: str | None
    duration_ms: int | None
    thumbnail_url: str | None
    item_type: str
    published_at: datetime | None
    channel_name: str | None
    subtitle_languages: list[str]


class CreateSourceItemRequest(BaseModel):
    url: HttpUrl


class SourceItemResponse(BaseModel):
    id: int
    source_account_id: int
    external_item_id: str
    item_type: str
    title: str | None
    canonical_url: str | None
    thumbnail_url: str | None
    published_at: datetime | None
    duration_ms: int | None
    status: str

    model_config = ConfigDict(from_attributes=True)  # E2：显式 ConfigDict，与 settings.py 风格一致


def _map_adapter_errors(exc: AdapterError) -> HTTPException:
    """F7：两端点共用的错误映射（400 白名单 / 502 上游失败 / 504 超时）。"""
    if isinstance(exc, UrlNotAllowedError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, AdapterTimeoutError):
        return HTTPException(status_code=504, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@router.post("/resolve-url", response_model=ResolveUrlResponse)
def resolve_url(
    body: ResolveUrlRequest,
    adapter: MediaSourceAdapter = Depends(get_media_adapter),
) -> ResolveUrlResponse:
    """RAD-022 第一步：解析预览，不落库。"""
    try:
        media = adapter.resolve(str(body.url))
    except AdapterError as exc:
        raise _map_adapter_errors(exc) from exc
    return ResolveUrlResponse(
        platform=media.platform, external_id=media.external_item_id, title=media.title,
        duration_ms=media.duration_ms, thumbnail_url=media.thumbnail_url,
        item_type=media.item_type, published_at=media.published_at,
        channel_name=media.channel_name,
        subtitle_languages=[t.language for t in media.subtitles],
    )


@router.post("", response_model=SourceItemResponse, status_code=201)
def create_source_item(
    body: CreateSourceItemRequest,
    session: Session = Depends(get_db),
    adapter: MediaSourceAdapter = Depends(get_media_adapter),
) -> SourceItemResponse:
    """RAD-022 第二步：确认后创建（服务端重新 resolve，C4）。"""
    try:
        item = discovery.create_item_from_url(str(body.url), session, adapter)
    except AdapterError as exc:
        raise _map_adapter_errors(exc) from exc
    return SourceItemResponse.model_validate(item)
```

`main.py` 追加：

```python
from app.api.v1 import api_router

app.include_router(api_router)
```

- [x] **Step 10.3 单测 + 集成用例通过；`make dev` 手测 OpenAPI（/docs 出现两个新操作）**

- [x] **Step 10.4 提交** `git commit -m "feat: 手工 URL 解析与创建 API（RAD-022）"`

---

### Task 11: Celery 发现任务 + 调度 + 镜像

**Files:**
- Create: `apps/api/app/worker/tasks.py`
- Modify: `apps/api/app/worker/celery_app.py`（beat_schedule）、`infra/docker/Dockerfile.api`（装 yt-dlp）、`Makefile`（worker-beat 目标）
- Test: `tests/integration/test_discover_tasks.py`

- [x] **Step 11.1 写失败测试**（任务函数直调 `.run()`，不依赖 broker）

```python
# tests/integration/test_discover_tasks.py
"""Celery 任务直调（task.run()）验证编排与 DB 副作用；broker 交互不在本层测。"""
# 复用 Task 9 的 StubAdapter/账号 setup 辅助（抽到 tests/_stubs.py 或 conftest fixture）

def test_discover_source_account_task_runs_end_to_end(db_session, monkeypatch):
    ...  # 造 auto_poll 账号 → monkeypatch tasks.build_adapter → StubAdapter
         # 直调 discover_source_account.run(account_id=...)
         # 断言条目入库 + send_task 被调（monkeypatch celery_app.send_task 收集）

def test_dispatch_due_discovers_only_due_enabled(db_session, monkeypatch):
    ...  # 造 3 账号：due+enabled / not-due(last_success_at 刚刚) / disabled
         # 断言只对 due+enabled 账号 send_task("discover_source_account")

def test_failure_marks_task_failed(db_session, monkeypatch):
    ...  # StubAdapter 抛 → pytest.raises(AdapterError)（任务直调透传）+ failure_count==1
```

- [x] **Step 11.2 确认失败 → 实现**

```python
# apps/api/app/worker/tasks.py
"""EPIC-02 Celery 任务：账号发现 + 到期派发。"""

from app.db.session import get_session_factory
from app.services import discovery
from app.services.media.contracts import MediaSourceAdapter
from app.worker.celery_app import celery_app


def build_adapter() -> MediaSourceAdapter:
    # F1：工厂在 services/media，worker 不依赖 app.api
    from app.services.media.factory import get_media_adapter
    return get_media_adapter()


@celery_app.task(name="discover_source_account")  # F5：无自动重试——失败计数在编排层，重试会双计
def discover_source_account(account_id: int) -> dict:
    session = get_session_factory()()
    try:
        return discovery.discover_account(
            account_id, session, build_adapter(), send=celery_app.send_task,
        )
    finally:
        session.close()


@celery_app.task(name="dispatch_due_discoveries")
def dispatch_due_discoveries() -> int:
    """C6：扫描 enabled 且到期（last_success_at + poll_interval_sec < now）的账号并派发。"""
    from datetime import UTC, datetime

    from sqlalchemy import or_, select, text

    from app.db.models import SourceAccount

    session = get_session_factory()()
    try:
        due = session.scalars(
            select(SourceAccount).where(
                SourceAccount.enabled.is_(True),
                or_(
                    SourceAccount.last_success_at.is_(None),
                    text("source_account.last_success_at + (source_account.poll_interval_sec * interval '1 second') < now()"),
                ),
            )
        ).all()
        for account in due:
            celery_app.send_task("discover_source_account", args=[account.id])
        return len(due)
    finally:
        session.close()
```

（E5：到期判定**必须**下沉为 `SourceAccountRepository.list_due(now)`——repo 一条可测方法（含 due/ enabled/disabled 三态单测）+ 任务薄壳。）

`celery_app.py` 追加：

```python
celery_app.conf.beat_schedule = {
    "dispatch-due-discoveries": {
        "task": "dispatch_due_discoveries",
        "schedule": get_settings().discover_dispatch_interval_sec,
    }
}
```

`Dockerfile.api` 层 1 追加（pip 装 yt-dlp CLI，版本下限对齐本机 2026.03.17）：

```dockerfile
RUN mkdir -p app && touch app/__init__.py \
    && pip install --no-cache-dir . "yt-dlp>=2026.3.17" \
    && rm -rf app
```

`Makefile` 追加：

```make
worker-beat: ## 启动 Celery worker + beat（来源发现调度）
	$(PYTHON) -m celery -A app.worker.celery_app worker --beat --loglevel=info
```

- [x] **Step 11.3 集成测试通过；`docker build -f infra/docker/Dockerfile.api .` 本地过**

- [x] **Step 11.3b beat 配置断言（Eng GAP-4）**：`tests/integration/test_discover_tasks.py` 追加——

```python
def test_beat_schedule_wired():
    from app.worker.celery_app import celery_app
    sched = celery_app.conf.beat_schedule.get("dispatch-due-discoveries")
    assert sched is not None and sched["task"] == "dispatch_due_discoveries"
    assert sched["schedule"] > 0
```

- [x] **Step 11.4 EPIC-03 契约注记（F4）**：本计划向下游投递的 `prepare_source_item(item_id)` 必须满足——
  ① 任务幂等（同 item 重复投递安全：并发 discover 双判"新建"会双投）；② EPIC-03 首个任务需补扫存量
  `status='discovered'` 条目（G1：commit 后 send_task 失败不重投）。写入执行计划 EPIC-03 章节顶部注记。

- [x] **Step 11.5 提交** `git commit -m "feat: 账号发现 Celery 任务与到期派发调度（RAD-023）"`

---

### Task 12: ADR-0007 + 文档 + 全量校验收尾

**Files:**
- Create: `docs/adr/0007-ytdlp-subprocess-and-sync-adapter.md`
- Modify: `.env.example`、`README.md`（已完成/TODO/目录结构/排障）、本计划文档勾选

- [x] **Step 12.1 ADR-0007**（Context/Decision/Consequences 三段：C1 同步契约偏离执行计划 async 伪码、C2 子进程而非库、超时与白名单安全边界、真异步切换挂 ADR-0005 阈值）

- [x] **Step 12.2 `.env.example` 追加**（含注释说明默认值即开箱可用）：

```bash
# --- Media Discovery (EPIC-02) ---
# YTDLP_BINARY=yt-dlp
# YTDLP_TIMEOUT_SEC=60
# MEDIA_HOST_ALLOWLIST=youtube.com,youtu.be,bilibili.com,douyin.com
# DISCOVER_PLAYLIST_MAX_ITEMS=50
# DISCOVER_DISPATCH_INTERVAL_SEC=300
```

- [x] **Step 12.3 README**：已完成加 RAD-020~023 行；TODO 删"ingestion 管道"改 EPIC-03+；目录结构补 services/api 层；排障加两行（yt-dlp 未安装→`YTDLP_BINARY` 指路/镜像内置；resolve-url 400 白名单→MEDIA_HOST_ALLOWLIST）；快速开始后加"手工解析一个视频"用法段（DX1）：

```bash
curl -fsS -X POST localhost:8000/api/v1/source-items/resolve-url   -H 'content-type: application/json'   -d '{"url":"https://www.youtube.com/watch?v=<视频id>"}'
# {"platform":"youtube","external_id":"…","title":"…","duration_ms":…,
#  "thumbnail_url":"…","item_type":"vod","published_at":"…","channel_name":"…",
#  "subtitle_languages":["zh-Hans","en"]}
```

（示例含真实响应样例；确认无误后 POST /api/v1/source-items 同 body 落库。定时发现：`make worker-beat` 后 tail 日志观察 `discover_ok` / `discover_failed`。）

- [x] **Step 12.4 全量验证矩阵**：

```bash
make lint                                    # ruff + mypy 全绿
.venv/bin/python -m pytest                   # 全部单测+集成（需 compose 栈）
docker build -f infra/docker/Dockerfile.api . # 含 yt-dlp 的镜像可构建
curl -fsS localhost:8000/api/v1/health       # 回归 D19 契约
git push && gh run watch                     # CI 6/6 绿
```

- [x] **Step 12.5 提交** `git commit -m "docs: ADR-0007 与 EPIC-02 文档收尾"`

---

## 验收（对照执行计划）

- RAD-020：`contracts.py` 四方法 Protocol + 统一数据类，核心域零平台 SDK 依赖 ✅
- RAD-021：子进程受控调用、超时、stderr 捕获、无 shell、URL 白名单、统一 ResolvedMedia；五类 fixture 全测 ✅
- RAD-022：`POST /api/v1/source-items/resolve-url`（预览）+ `POST /api/v1/source-items`（确认创建）✅
- RAD-023：`discover_source_account(account_id)` 六步逻辑 + 幂等唯一键 + last_success_at/failure_count ✅
- 额外闭环：到期派发 beat（C6）、镜像含 yt-dlp、Plan #1 遗留清偿（C8）

## 风险与不做

- **不做**：真实网络抓取的自动化测试（CI 无外网、平台反爬不稳）——真实链路靠本地手测 + EPIC-09 质量评估再收口
- **不做**：cookies/登录态、B 站分页全量（playlist 窗口封顶 50）、抖音分享短链展开（b23.tv/v.douyin.com 白名单先不含，需要时配置即可）
- **风险**：yt-dlp 对平台改版敏感——版本下限钉在镜像，坏掉时升级镜像重 build 即可
- **风险**：CI 上无 yt-dlp 真二进制——所有适配器测试走假二进制，docker-build 作业验证真安装

<!-- autoplan-accepted:ceo -->
- F1: adapter 工厂落 `app/services/media/factory.py`；`app/api` 与 `app/worker/tasks.py` 各自从该处导入；验证=mypy 无 app.api←app.worker 依赖 + 测试通过
- F2: `discover_account` 编排边界 `except Exception`：failure_count+=1、structlog 记 account_id/异常、commit 后重抛；验证=test_discover_account_failure_increments_counter（抛 RuntimeError 亦计数）
- F3: README 排障/安全注记"端点无鉴权，仅限本地/内网"；验证=README diff
- F4: Task 11 计划文字加入"EPIC-03 契约：prepare_source_item 必须幂等（同 item 重复投递安全）"；验证=计划文件
- F5: 任务定义去掉 bind=True/max_retries/default_retry_delay；验证=代码无死配置
- F6: worker 任务与 discovery 服务入口/出口/失败三处 structlog 日志（account_id、counts、错误）；验证=测试断言或日志存在性检查
- F7(helper): 两端点共用错误映射 helper；验证=单测覆盖 400/502/504 各一次即可
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:dx -->
- DX1: Task 12 的 README 步骤追加——快速开始含 `curl -fsS -X POST localhost:8000/api/v1/source-items/resolve-url -H 'content-type: application/json' -d '{"url":"https://www.youtube.com/watch?v=<id>"}'` 示例及真实响应样例；另加一行"make worker-beat 后 tail 日志观察 discover_ok/failure"；验证=README diff 含可复制的完整命令与样例响应
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- E3: `ResolvedMedia` 增 `channel_url: str | None = None` 字段；`_parse_resolved` 填 `data.get("channel_url")`；`create_item_from_url` 用 `media.channel_url`；验证=test_discovery_service 断言 account.url 为 stub 提供的 channel_url（SUCCESS_PAYLOAD 加 channel_url 键）
- E1: Task 5 测试 helper 改 monkeypatch 签名 + `run_json` 方法名；五个用例签名同步；验证=test_ytdlp_process 全绿且无 os.environ 直接操作
- E2/E4/E5/E6: ConfigDict、payloads 共享模块、list_due 下沉（含三态单测）、settings 唯一实现——分别以对应测试/代码规范落地；验证=make lint + mypy + 全量 pytest
- GAP-1: `test_missing_binary_raises_with_hint`（match="YTDLP_BINARY"）入 test_ytdlp_process
- GAP-2: `test_resolve_published_at_from_unix_timestamp` 入 test_ytdlp_adapter_resolve
- GAP-3: `test_resolve_url_timeout_504` 入 test_api_source_items
- GAP-4: `test_beat_schedule_wired` 入 test_discover_tasks
<!-- /autoplan-accepted:eng -->

## 实施记录（2026-09-17）

12 任务全部完成，12 个提交（6658f08…8c2810b）。90 tests / lint+mypy 绿 / docker 镜像含 yt-dlp 构建成功 / health 契约回归 OK / CI 35159744311 6/6 绿。过程中修正（偏离计划的实现细节，均为外科式）：
- Task 10：端点改 `Annotated[..., Depends(...)]`（ruff B008）；集成测试需同时 override `get_db`（否则 TestClient 写开发库，已清理误写行）
- Task 11：`list_due` 去 now 入参（用 `func.now()` DB 时钟防多机漂移）；任务测试 monkeypatch `worker_tasks.get_session_factory` 指向 radar_test
- 模型无 relationship：集成测试经 `creator_id` 显式查 Creator（Plan #1 未建关系，符合现状）
## Review record

<!-- autoplan:ceo (SELECTIVE EXPANSION, 2026-09-17) — native in-host; Codex outside voice unavailable (model_unusable); Claude subagent skipped per user standing order (no subagents in this repo) -->

INPUT: ceo 454b2f532bd3e28eac50c17b8a7ee183e8746b740998a592b8539ce21ddef3e9

### 0A 前提评估

| 前提 | 判定 |
|---|---|
| 系统需要外部内容发现管道（否则产品只有种子数据） | 成立——整条产品链的入口 |
| yt-dlp 作为通用提取器 | 成立——PRD 5.1 已决策，Layer-1 成熟工具 |
| 同步 Adapter 契约（C1，偏离执行计划 async 伪码） | 成立——与 ADR-0002 sync 栈一致；执行计划伪码本身与 PRD 5.3 矛盾 |
| 手工创建自动 get-or-create（C5） | 成立但留味道：跨平台同名频道会并入同一 creator——EPIC-04 实体归一化收口（Taste T3 呈交大门） |
| 创建时服务端二次 resolve（C4） | 成立——管理员工具，正确性 > 双倍解析成本 |

无"明显错误"前提 → 无 User Challenge 入队。

### 0B 既有代码利用

| 子问题 | 复用 |
|---|---|
| 账号幂等 upsert | `SourceAccountRepository.upsert_by_external`（D39）直接复用 |
| 仓储基类 | `BaseRepository` 扩展 |
| DB session/错误转译 | `get_db`/`get_session_factory` 原样复用 |
| Celery | `celery_app` 扩展 beat |
| creator get-or-create | `CreatorRepository.get_by_name/create` 复用 |

零重复建设。

### 0C Dream State Delta

```
CURRENT                     THIS PLAN                    12-MONTH IDEAL
14 张表+种子数据，         contract + yt-dlp 子进程     多平台 VOD/直播摄入 →
无真实内容入口     --->    适配器 + 手工URL/定时   --->  ASR→LLM观点→证据→
                           发现，source_item 有真实      共识时间线→雷达前端
                           数据流（status=discovered）
```

朝 12 个月理想态前进，无背离。`prepare_source_item` 挂点是 EPIC-03 的地基（平台潜力已具备）。

### 0C-bis 实现备选

| 方案 | 概要 | 工作量 | 风险 |
|---|---|---|---|
| A 子进程 CLI + Generic Adapter（本计划） | 受控 subprocess，镜像钉版本 | M | 低-中 |
| B yt-dlp Python 库内嵌 | 进程内调用 | S-M | 中（崩溃隔离/内存/杀超时难） |
| C 平台专属 SDK Adapter 现在就拆 | Youtube/B站/抖音各写一个 | L | 中（PRD 明确推迟） |
| 最小可行：只做手工 URL，砍调度 | RAD-023 缺席 | S | poll_interval_sec 变死字段 |

推荐 A：P1 完整 + P5 显式；RAD-021 的超时/stderr/无 shell 要求在 subprocess 路径上天然满足。

### 0E 时间轴审问（实现时会撞上的决定，现在钉死）

- upsert 新建标志：**SELECT→命中即更新返回 False；未命中 ON CONFLICT DO UPDATE 兜并发后回查返回 True**（P5 显式优于 xmax 技巧）
- 任务失败计数在编排层 try/except 边界做（见 F2），不做 Celery 自动重试（避免双计数）
- fixture 需 `chmod +x` 且 `tests/fixtures/__init__.py` 存在；migrations 需补 `__init__.py`（计划已注）
- HttpUrl 校验拒绝裸串；guard 二次校验主机白名单——两层各司其职

### 审查章节结论（1-10；11 无 UI 范围跳过）

1. **架构**：分层清晰（api→discovery→{repos,adapter}；worker→discovery；contracts 无 ORM 依赖，url_guard 仅依赖 contracts，无环）。发现 F1：adapter 工厂 `get_media_adapter` 放 `app/api/__init__.py` 会使 worker 依赖 api 包——移至 `app/services/media/factory.py`，api 与 worker 各自导入。已采纳。
2. **错误与救援**：见下方注册表。发现 F2：计划中任务失败只捕 `AdapterError`，但测试用例抛 `RuntimeError`——改为编排边界 `except Exception` 记 failure_count + 结构化日志（account_id、错误上下文）后**重抛**（边界捕获+重抛是合法模式，非吞错）。缺口 G1：commit 后 send_task 失败则新条目停在 discovered（重新 discover 不会补投）——V1 记录为已知限制，EPIC-03 落地全量扫描任务时收口（outbox 模式明确不做，过度设计）。
3. **安全**：新端点无鉴权/无限流（V1 全仓姿态，PRD 3.4 角色留待后续）；本地 dev uvicorn 默认绑 127.0.0.1；命令注入面关闭（argv 列表、无 shell、URL 白名单先于子进程）；README 记录"上线前必须加鉴权"（F3 采纳为文档动作，EPIC-07/10 落地）。无新增密钥。
4. **数据流/边界**：双击/重复 POST 幂等 ✓；同账号并发 discover 均判"新建"会双投 prepare——F4 采纳：计划写入 EPIC-03 契约要求"prepare_source_item 必须幂等"；flat 条目无日期、live 无时长均已处理 ✓。
5. **代码质量**：两端点 try/except 错误映射重复——采纳抽取 `_map_adapter_errors` helper（P4）；F5：任务 `bind=True, max_retries=2` 是死配置（无 self.retry，且自动重试会双计 failure_count）——移除。
6. **测试**：RAD-021 五类 fixture 全覆盖；幂等/派发/失败计数/错误映射均有断言；超时测试用 sleep+短 timeout，确定性 ✓。金字塔：单元重、集成分层合理 ✓。无缺口（prepare 幂等属 EPIC-03 义务，F4 已转为契约文字）。
7. **性能**：playlist 封顶 50；每 resolve 一次子进程（管理员工具 p99 由网络主导，可接受）；连接池共用既有 engine；无 N+1。
8. **可观测**：F6（采纳）：新代码零日志——task 入口/出口/失败补 structlog（account_id、discovered/created 计数、stderr 尾部已在异常消息中）；metrics/告警按 PRD 留 EPIC-10。
9. **部署**：纯增量代码零迁移；镜像层1 pip 装 yt-dlp（PIP_INDEX_URL ARG 已有，mirror 兼容）；回滚=git revert；docker-build 作业即冒烟。
10. **长期**：可逆性 5/5；债务三项全部有界（NotImplementedError→EPIC-03、creator 同名合并→EPIC-04、投递缺口→EPIC-03）；12 个月后新工程师可读性良好。

### Error & Rescue Registry（Section 2 产出）

| 代码路径 | 可能出错 | 异常类 | 捕获? | 处置 | 用户所见 |
|---|---|---|---|---|---|
| url_guard.ensure_allowed_url | 协议/主机/userinfo 非法 | UrlNotAllowedError | Y | API→400；adapter 前置拒绝 | 明确白名单提示 |
| YtDlpProcess.run_json | 非零退出（私有/坏URL） | AdapterProcessError | Y | API→502（带 stderr 尾部） | 上游失败详情 |
| YtDlpProcess.run_json | stdout 非 JSON | AdapterProcessError | Y | 同上 | 同上 |
| YtDlpProcess.run_json | 超时 | AdapterTimeoutError | Y | API→504 | 超时提示 |
| YtDlpProcess.run_json | 二进制缺失 | AdapterProcessError | Y | 消息含 YTDLP_BINARY 指引 | 配置指引 |
| discover（account.url 空） | 无 URL | AdapterError | Y | 任务失败计数 | 任务失败可见 |
| discover_account 编排 | 任何适配器/运行时错误 | Exception（F2） | Y | failure_count+1、日志、重抛 | 任务标失败 |
| upsert 并发插入 | 唯一键冲突 | IntegrityError | Y(隐式) | ON CONFLICT DO UPDATE 序列化 | 无感 |
| send_task（commit 后） | Redis 不可达 | OperationalError | **N（G1 已知限制）** | 日志；EPIC-03 扫描兜底 | 条目延迟处理 |
| POST 端点中途 DB 失败 | 连接断开 | OperationalError | 部分（get_db 仅转译首连） | 500 | V1 接受 |

### Failure Modes Registry

| 代码路径 | 失败模式 | 已救援? | 有测试? | 用户所见 | 有日志? |
|---|---|---|---|---|---|
| resolve | 私有视频 | Y | Y | 502 | Y(异常链) |
| resolve | 超时 | Y | Y | 504 | Y |
| resolve | 白名单拒绝 | Y | Y | 400 | Y |
| discover | 播放列表空/坏条目 | Y | Y | 空/跳过 | Y |
| 任务 | 适配器崩溃 | Y | Y | 任务失败 | Y（F6 后） |
| 任务 | send 失败 | N（G1） | N | 条目滞留 discovered | Y |
| API | 重复提交 | Y(幂等) | Y | 同一行 | — |

**CRITICAL GAP：0**（G1 为已记录的已知限制，非静默：有日志 + EPIC-03 兜底计划）。

### NOT in scope（本计划不做，含理由）

- 平台专属 Adapter（Youtube/B站/抖音拆分）——PRD 5.3：generic 先行，失效时再替换
- cookies/登录态、B 站全量分页、短链展开（b23.tv/v.douyin.com）——V1 非必须，白名单可配置放行
- 真实网络抓取自动化测试——CI 无外网且平台反爬不稳；本地手测 + EPIC-09 收口
- outbox/outbox-like 投递保障——G1 用日志+EPIC-03 扫描兜底
- 端点鉴权与限流——全仓 V1 姿态，EPIC-07/10；README 记录风险
- metrics/告警/dashboard——EPIC-10

### What already exists

见 0B 表——本计划零重复建设，全部为扩展性复用。

### Deferred to TODOS.md / 后续 EPIC

- F3 鉴权限流 → EPIC-07（API）/EPIC-10（运维）
- G1 投递缺口 → EPIC-03 首个任务加"prepare_source_item 幂等 + 存量 discovered 条目补扫"
- creator 同名合并 → EPIC-04 实体归一化
- F4 已转为计划内 EPIC-03 契约注记（见 Task 11 修订）

### Scope Proposals（SELECTIVE EXPANSION 樱桃挑选）

- 提出 1 项：手动触发发现端点 `POST /api/v1/source-accounts/{id}/discover`（~30min，便于 /qa 真实环境验证不必等 beat）→ **未采纳**（YAGNI：beat 5 分钟间隔足够；留 Taste T2 由用户在大门裁决）
- 其余 delight 候选（列表端点、限流、指标）均越界 EPIC-07/10 → NOT in scope

### CEO 大门前的 Taste 决策（呈交 Phase 4）

- T1：契约含 NotImplementedError 占位方法（完整契约）vs 只定义已实现方法（纯 YAGNI）——推荐前者
- T2：手动 discover 触发端点是否纳入——推荐不纳入
- T3：creator 按名 get-or-create 的同名合并风险 V1 接受——推荐接受

### Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|----------|
| C-1 | ceo | F1 adapter 工厂移 services/media，worker 不依赖 api 包 | Mechanical | P5 | 分层正确性 | 留在 app/api |
| C-2 | ceo | F2 失败计数边界 catch Exception+日志+重抛 | Mechanical | P1/P5 | 边界捕获合法且测试对齐 | 只捕 AdapterError |
| C-3 | ceo | G1 投递缺口文档化+EPIC-03 兜底 | Taste(轻) | P3/P6 | outbox 过度设计 | outbox 模式 |
| C-4 | ceo | F3 鉴权限流 README 记录，EPIC-07/10 落地 | Mechanical | P3 | 全仓 V1 姿态一致 | 现在加 |
| C-5 | ceo | F4 prepare 幂等写入 EPIC-03 契约注记 | Mechanical | P1 | 并发双投真实存在 | 忽略 |
| C-6 | ceo | F5 移除任务死重试配置 | Mechanical | P5 | 无 retry 调用的配置是噪音 | 保留 |
| C-7 | ceo | 两端点错误映射抽 helper | Mechanical | P4 | 同一映射写两遍 | 各写各的 |
| C-8 | ceo | F6 新代码补 structlog 日志 | Mechanical | P1 | 可观测非可选 | 无日志 |
| C-9 | ceo | upsert 新建判定用 SELECT→ON CONFLICT 兜底 | Mechanical | P5 | 显式可读 | xmax 技巧 |
| C-10 | ceo | 手动 discover 端点不纳入 | Taste | YAGNI/P3 | beat 足够 | 纳入（挂 T2） |

<!-- autoplan-baseline-edits:ceo {"sourceSha256":"454b2f532bd3e28eac50c17b8a7ee183e8746b740998a592b8539ce21ddef3e9","replacements":[{"oldText":"apps/api/app/api/__init__.py                    # get_media_adapter 依赖\napps/api/app/api/v1/__init__.py                 # api_router","newText":"apps/api/app/services/media/factory.py          # get_media_adapter 工厂（F1：api/worker 共用，互不依赖）\napps/api/app/api/__init__.py                    # 空（包标记）\napps/api/app/api/v1/__init__.py                 # api_router"},{"oldText":"```python\n# apps/api/app/api/__init__.py\nfrom functools import lru_cache","newText":"```python\n# apps/api/app/services/media/factory.py  （F1：独立于 api/worker，二者共用）\nfrom functools import lru_cache"},{"oldText":"def _install(monkeypatch, adapter):\n    from app.api import get_media_adapter\n    monkeypatch.setattr(\"app.api.get_media_adapter\", lambda: adapter)\n    # 端点用 Depends(get_media_adapter) → 需走 dependency_overrides：\n    app.dependency_overrides[get_media_adapter] = lambda: adapter","newText":"def _install(monkeypatch, adapter):\n    from app.services.media.factory import get_media_adapter\n    # 端点用 Depends(get_media_adapter) → 走 dependency_overrides：\n    app.dependency_overrides[get_media_adapter] = lambda: adapter"},{"oldText":"def build_adapter() -> MediaSourceAdapter:\n    # 独立工厂而非直接用 api 的 lru_cache：worker 进程与 API 进程解耦\n    from app.api import get_media_adapter\n    return get_media_adapter()\n\n\n@celery_app.task(name=\"discover_source_account\", bind=True, max_retries=2, default_retry_delay=60)\ndef discover_source_account(self, account_id: int) -> dict:","newText":"def build_adapter() -> MediaSourceAdapter:\n    # F1：工厂在 services/media，worker 不依赖 app.api\n    from app.services.media.factory import get_media_adapter\n    return get_media_adapter()\n\n\n@celery_app.task(name=\"discover_source_account\")  # F5：无自动重试——失败计数在编排层，重试会双计\ndef discover_source_account(account_id: int) -> dict:"},{"oldText":"失败计数语义（与测试对齐）：AdapterError/RuntimeError 令 `failure_count += 1` 后**重抛**（Celery 标记任务失败）。实现放 try/except 包住 discover+upsert：\n\n```python\n    try:\n        items = adapter.discover(...)\n        ...（upsert/派发/成功标记/commit）\n    except AdapterError:\n        account.failure_count += 1\n        session.commit()\n        raise\n```\n\n（注意 upsert 半途异常需 rollback 后再累加计数并 commit，测试用 rollback+重取验证。）","newText":"失败计数语义（与测试对齐，F2）：任何异常令 `failure_count += 1` 后**重抛**（Celery 标记任务失败）。\n捕获点是编排边界 `except Exception`——任务边界的合法模式：记完整上下文日志后重抛，绝不吞错。\nCelery 自动重试明确不用（会双计 failure_count）：\n\n```python\n    import structlog\n    logger = structlog.get_logger(__name__)\n    try:\n        items = adapter.discover(...)\n        ...（upsert/派发/成功标记/commit）\n    except Exception:\n        session.rollback()\n        account.failure_count += 1\n        # F6：结构化日志——排障时能从日志重建现场\n        logger.exception(\"discover_failed\", account_id=account_id, platform=account.platform)\n        session.commit()\n        raise\n```"},{"oldText":"    account.last_success_at = datetime.now(UTC)\n    account.failure_count = 0\n    session.commit()\n    return {\n        \"account_id\": account_id, \"skipped\": False,\n        \"discovered\": len(items), \"created\": len(created_ids),\n    }","newText":"    account.last_success_at = datetime.now(UTC)\n    account.failure_count = 0\n    session.commit()\n    logger.info(\"discover_ok\", account_id=account_id, discovered=len(items), created=len(created_ids))\n    return {\n        \"account_id\": account_id, \"skipped\": False,\n        \"discovered\": len(items), \"created\": len(created_ids),\n    }"},{"oldText":"@router.post(\"/resolve-url\", response_model=ResolveUrlResponse)\ndef resolve_url(\n    body: ResolveUrlRequest,\n    adapter: MediaSourceAdapter = Depends(get_media_adapter),\n) -> ResolveUrlResponse:\n    \"\"\"RAD-022 第一步：解析预览，不落库。\"\"\"\n    try:\n        media = adapter.resolve(str(body.url))\n    except UrlNotAllowedError as exc:\n        raise HTTPException(status_code=400, detail=str(exc)) from exc\n    except AdapterTimeoutError as exc:\n        raise HTTPException(status_code=504, detail=str(exc)) from exc\n    except AdapterProcessError as exc:\n        raise HTTPException(status_code=502, detail=str(exc)) from exc\n    return ResolveUrlResponse(","newText":"def _map_adapter_errors(exc: AdapterError) -> HTTPException:\n    \"\"\"F7：两端点共用的错误映射（400 白名单 / 502 上游失败 / 504 超时）。\"\"\"\n    if isinstance(exc, UrlNotAllowedError):\n        return HTTPException(status_code=400, detail=str(exc))\n    if isinstance(exc, AdapterTimeoutError):\n        return HTTPException(status_code=504, detail=str(exc))\n    return HTTPException(status_code=502, detail=str(exc))\n\n\n@router.post(\"/resolve-url\", response_model=ResolveUrlResponse)\ndef resolve_url(\n    body: ResolveUrlRequest,\n    adapter: MediaSourceAdapter = Depends(get_media_adapter),\n) -> ResolveUrlResponse:\n    \"\"\"RAD-022 第一步：解析预览，不落库。\"\"\"\n    try:\n        media = adapter.resolve(str(body.url))\n    except AdapterError as exc:\n        raise _map_adapter_errors(exc) from exc\n    return ResolveUrlResponse("},{"oldText":"    \"\"\"RAD-022 第二步：确认后创建（服务端重新 resolve，C4）。\"\"\"\n    try:\n        item = discovery.create_item_from_url(str(body.url), session, adapter)\n    except UrlNotAllowedError as exc:\n        raise HTTPException(status_code=400, detail=str(exc)) from exc\n    except AdapterTimeoutError as exc:\n        raise HTTPException(status_code=504, detail=str(exc)) from exc\n    except AdapterProcessError as exc:\n        raise HTTPException(status_code=502, detail=str(exc)) from exc\n    return SourceItemResponse.model_validate(item)","newText":"    \"\"\"RAD-022 第二步：确认后创建（服务端重新 resolve，C4）。\"\"\"\n    try:\n        item = discovery.create_item_from_url(str(body.url), session, adapter)\n    except AdapterError as exc:\n        raise _map_adapter_errors(exc) from exc\n    return SourceItemResponse.model_validate(item)"},{"oldText":"from app.api import get_media_adapter\nfrom app.db.session import get_db","newText":"from app.db.session import get_db\nfrom app.services.media.factory import get_media_adapter"},{"oldText":"- [ ] **Step 11.4 提交** `git commit -m \"feat: 账号发现 Celery 任务与到期派发调度（RAD-023）\"`","newText":"- [ ] **Step 11.4 EPIC-03 契约注记（F4）**：本计划向下游投递的 `prepare_source_item(item_id)` 必须满足——\n  ① 任务幂等（同 item 重复投递安全：并发 discover 双判\"新建\"会双投）；② EPIC-03 首个任务需补扫存量\n  `status='discovered'` 条目（G1：commit 后 send_task 失败不重投）。写入执行计划 EPIC-03 章节顶部注记。\n\n- [ ] **Step 11.5 提交** `git commit -m \"feat: 账号发现 Celery 任务与到期派发调度（RAD-023）\"`"}]} -->
<!-- autoplan-accepted:ceo -->
- F1: adapter 工厂落 `app/services/media/factory.py`；`app/api` 与 `app/worker/tasks.py` 各自从该处导入；验证=mypy 无 app.api←app.worker 依赖 + 测试通过
- F2: `discover_account` 编排边界 `except Exception`：failure_count+=1、structlog 记 account_id/异常、commit 后重抛；验证=test_discover_account_failure_increments_counter（抛 RuntimeError 亦计数）
- F3: README 排障/安全注记"端点无鉴权，仅限本地/内网"；验证=README diff
- F4: Task 11 计划文字加入"EPIC-03 契约：prepare_source_item 必须幂等（同 item 重复投递安全）"；验证=计划文件
- F5: 任务定义去掉 bind=True/max_retries/default_retry_delay；验证=代码无死配置
- F6: worker 任务与 discovery 服务入口/出口/失败三处 structlog 日志（account_id、counts、错误）；验证=测试断言或日志存在性检查
- F7(helper): 两端点共用错误映射 helper；验证=单测覆盖 400/502/504 各一次即可
<!-- /autoplan-accepted:ceo -->

### CEO Completion Summary

```
+====================================================================+
|            MEGA PLAN REVIEW — COMPLETION SUMMARY                   |
+====================================================================+
| Mode selected        | SELECTIVE EXPANSION（autoplan 固定）         |
| System Audit         | 仓库干净：无 TODO/FIXME 残留，CI 6/6 绿     |
| Step 0               | 5 前提全部成立；4 备选方案，A 维持          |
| Section 1  (Arch)    | 1 issue（F1 工厂位置）已采纳                |
| Section 2  (Errors)  | 10 error paths mapped, 1 已知限制(G1)       |
| Section 3  (Security)| 1 issue（F3 鉴权），0 High（本地绑定）      |
| Section 4  (Data/UX) | 7 edge cases mapped, 0 unhandled（F4 转注记）|
| Section 5  (Quality) | 2 issues（F5 死配置/DRY）已采纳             |
| Section 6  (Tests)   | Diagram produced, 0 gaps                    |
| Section 7  (Perf)    | 0 issues（playlist 封顶/无 N+1）            |
| Section 8  (Observ)  | 1 gap（F6 日志）已采纳                      |
| Section 9  (Deploy)  | 0 risks（纯增量，零迁移）                   |
| Section 10 (Future)  | Reversibility: 5/5, debt items: 3（全部有界）|
| Section 11 (Design)  | SKIPPED (no UI scope)                       |
+--------------------------------------------------------------------+
| NOT in scope         | written (6 items)                           |
| What already exists  | written                                     |
| Dream state delta    | written                                     |
| Error/rescue registry| 10 methods, 0 CRITICAL GAPS                 |
| Failure modes        | 7 total, 0 CRITICAL GAPS                    |
| TODOS.md updates     | 0 新增（挂后续 EPIC，已列 Deferred）        |
| Scope proposals      | 1 proposed, 0 accepted                      |
| CEO plan             | skipped（无扩张性采纳）                     |
| Outside voice        | codex unavailable (model_unusable)          |
| Lake Score           | 10/10（全部采纳完整选项）                   |
| Diagrams produced    | 1 (dream state)                             |
| Stale diagrams found | 0                                           |
| Unresolved decisions | 3 taste（T1/T2/T3 → 大门）                  |
+====================================================================+
```

CEO DUAL VOICES — CONSENSUS TABLE:
```
  Dimension                           Claude(in-host)  Codex   Consensus
  ──────────────────────────────────── ──────────────── ─────── ─────────
  1. Premises valid?                   是               N/A     N/A（无外审）
  2. Right problem to solve?           是               N/A     N/A
  3. Scope calibration correct?        是               N/A     N/A
  4. Alternatives sufficiently explored?是              N/A     N/A
  5. Competitive/market risks covered? N/A（内部工具）  N/A     N/A
  6. 6-month trajectory sound?         是               N/A     N/A
```
外审不可用（codex model_unusable；Claude subagent 按用户指令不派发）→ 六格共识 N/A，永不记 CONFIRMED。对抗性自查（作为弱替代）追加执行：未发现 F1-F6 之外的新问题（--flat-playlist/--dump-single-json 组合、pydantic HttpUrl 归一化、beat 按名注册均核实可行）。

### Implementation Tasks（CEO 阶段，已并入计划修订）

本阶段全部采纳项已写入上方 accepted 块并将在 Eng 阶段后统一落入计划任务清单；无独立新增任务文件。

<!-- autoplan:dx (DX POLISH, 2026-09-17) — native in-host; Codex unavailable (model_unusable); Claude subagent skipped per user standing order -->

INPUT: dx 6d8c7a4e291e74c127862dc0ebc689f7106d608fd215e123323d6899b3169d64

### 产品类型与人物设定（Step 0）

- 类型：API/Service（内部：两个 HTTP 端点 + make 工作流），非对外 SDK
- 人物（0A，从 README 推断）：本仓库后端贡献者（Python + make，兼运维）——clone → make setup/bootstrap/dev → curl；期望 README/排障表即开箱，容忍度≈一次排障表检索
- 同理心叙事（0B）：我 clone 仓库跑通 health 后想试新管道：README 里没有 resolve-url 的 curl 示例，只能翻 /docs；跑 make worker-beat 后前 5 分钟毫无反馈，要 tail 日志/查库才知道 discover 是否发生
- 基准（0C）：内部工具用参考基准（Stripe 30s / Vercel 2min / Docker 5min）；本计划 clone→resolve-url ≈ 5 分钟（Competitive 档），达标
- 魔法时刻（0D）：贴一个真实视频 URL，秒级返回标题/时长/字幕可用性——载体 B（copy-paste 演示命令）：README 快速开始加一条带真实响应样例的 curl 示例（DX1 落地）
- 模式（0E）：DX POLISH（autoplan 固定）

### 开发者旅程（0F，POLISH 全阶段）

| 阶段 | 动作 | 摩擦 | 状态 |
|---|---|---|---|
| Discover | README/执行计划 | — | ok |
| Install | make setup/bootstrap | yt-dlp 缺失指引已在排障表 | ok |
| Hello World | curl resolve-url | **README 无示例**（DX1） | fixed |
| Real Usage | make worker-beat + POST | beat 无即时反馈——F6 日志已有，README 补一句 tail 提示（DX1） | fixed |
| Debug | 400/502/504 detail + fixtures 假二进制复现 | — | ok |
| Upgrade | 纯增量零迁移，git revert 回滚 | CHANGELOG 未建档（EPIC-10） | deferred |

### 首次开发者角色扮演（0G）

T+0 make bootstrap ✓ → T+2 curl health ✓ → T+4 找 resolve-url 用法：README 没有，转 /docs（Swagger 可试，能走通但多一跳）→ T+6 make worker-beat，5 分钟后日志出现 discover_ok/失败，DB 有行。结论：全链路可走通；唯一实质摩擦=README 示例缺失 → DX1 采纳。

### 8 个 Pass 评分（0-10，POLISH）

| Pass | 评分 | 依据 |
|---|---|---|
| 1 Getting Started | 7→8 | DX1 修复示例缺失；10 分需 playground（内部工具不需要） |
| 2 API/CLI 设计 | 8 | RESTful 可猜、POST 幂等、错误码语义清楚；列表/分页属 EPIC-07 |
| 3 错误消息 | 9 | problem+cause+fix 四类全覆盖（白名单提示配置、stderr 尾部、超时阈值、YTDLP_BINARY 指引） |
| 4 文档 | 7→8 | /docs 自动生成 ✓；DX1 补 README 用法段 |
| 5 升级路径 | 8 | 零迁移纯增量；CHANGELOG 挂 EPIC-10 |
| 6 开发环境 | 9 | make 全套、CI 无外网（假二进制）、docker-build 冒烟 |
| 7 社区 | N/A | 内部仓库，不打分（说明而非跳过：无外部开发者受众） |
| 8 DX 度量 | 6 | 测试即反馈；使用埋点挂 EPIC-10 观测 |

### DX Scorecard

| 维度 | 分数 | 趋势 |
|---|---|---|
| Getting Started | 8/10 | ↑（DX1） |
| API/CLI | 8/10 | — |
| Error Messages | 9/10 | — |
| Documentation | 8/10 | ↑（DX1） |
| Upgrade Path | 8/10 | — |
| Dev Environment | 9/10 | — |
| Community | N/A | 内部工具 |
| DX Measurement | 6/10 | deferred |
| **Overall** | **8/10** | — |
| TTHW | 5 min（Competitive 档，保持） | |
| 魔法时刻 | designed（curl 演示示例，DX1） | |

### NOT in scope（DX 维度）

- API 列表/分页/查询端点——EPIC-07 API 契约 epic
- CHANGELOG/版本化文档——EPIC-10
- 使用埋点/TTHW 度量——EPIC-10 观测

### What already exists（DX 维度）

README 快速开始/排障表（Plan #1 建立）、OpenAPI /docs 自动文档、make lint/test/dev/worker 工作流、假二进制 fixtures 可复现外部失败。

### Decision Audit Trail（dx）

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|----------|
| D-1 | dx | DX1：README 快速开始加 resolve-url curl 示例（真实响应样例）+ worker-beat 日志提示 | Mechanical | P1(战斗不确定) | 魔法时刻+消灭"翻 /docs"一跳，CC ~5min | 仅靠 /docs |
| D-2 | dx | Community pass 记 N/A 不打分 | Mechanical | P3 | 内部仓库无外部受众 | 硬打 0 分 |

<!-- autoplan-baseline-edits:dx {"sourceSha256":"7e28c5be8341cbacd94bc027a67fa5afbe6d5dd24c893a5ee17db4cce657835b","replacements":[{"oldText":"- [ ] **Step 12.3 README**：已完成加 RAD-020~023 行；TODO 删\"ingestion 管道\"改 EPIC-03+；目录结构补 services/api 层；排障加两行（yt-dlp 未安装→`YTDLP_BINARY` 指路/镜像内置；resolve-url 400 白名单→MEDIA_HOST_ALLOWLIST）","newText":"- [ ] **Step 12.3 README**：已完成加 RAD-020~023 行；TODO 删\"ingestion 管道\"改 EPIC-03+；目录结构补 services/api 层；排障加两行（yt-dlp 未安装→`YTDLP_BINARY` 指路/镜像内置；resolve-url 400 白名单→MEDIA_HOST_ALLOWLIST）；快速开始后加\"手工解析一个视频\"用法段（DX1）：\n\n```bash\ncurl -fsS -X POST localhost:8000/api/v1/source-items/resolve-url   -H 'content-type: application/json'   -d '{\"url\":\"https://www.youtube.com/watch?v=<视频id>\"}'\n# {\"platform\":\"youtube\",\"external_id\":\"…\",\"title\":\"…\",\"duration_ms\":…,\n#  \"thumbnail_url\":\"…\",\"item_type\":\"vod\",\"published_at\":\"…\",\"channel_name\":\"…\",\n#  \"subtitle_languages\":[\"zh-Hans\",\"en\"]}\n```\n\n（示例含真实响应样例；确认无误后 POST /api/v1/source-items 同 body 落库。定时发现：`make worker-beat` 后 tail 日志观察 `discover_ok` / `discover_failed`。）"}]} -->
<!-- autoplan-accepted:dx -->
- DX1: Task 12 的 README 步骤追加——快速开始含 `curl -fsS -X POST localhost:8000/api/v1/source-items/resolve-url -H 'content-type: application/json' -d '{"url":"https://www.youtube.com/watch?v=<id>"}'` 示例及真实响应样例；另加一行"make worker-beat 后 tail 日志观察 discover_ok/failure"；验证=README diff 含可复制的完整命令与样例响应
<!-- /autoplan-accepted:dx -->

DX DUAL VOICES — CONSENSUS TABLE:
```
  Dimension                           Claude(in-host)  Codex   Consensus
  ──────────────────────────────────── ──────────────── ─────── ─────────
  1. Getting started < 5 min?          是               N/A     N/A
  2. API/CLI naming guessable?         是               N/A     N/A
  3. Error messages actionable?        是               N/A     N/A
  4. Docs findable & complete?         DX1 后是         N/A     N/A
  5. Upgrade path safe?                是               N/A     N/A
  6. Dev environment friction-free?    是               N/A     N/A
```
外审不可用（codex model_unusable；subagent 按用户指令不派发）→ 共识格 N/A，永不 CONFIRMED。

<!-- autoplan:eng (FULL_REVIEW, 2026-09-17) — native in-host; Codex unavailable (model_unusable); Claude subagent skipped per user standing order -->

INPUT: eng a305bc76d48ebf98c869fdb1d751587657a1755392e641f2961e580f8f684eca

### Step 0 Scope Challenge（对照实际代码验证）

1. 既有代码复用（全部核实存在）：`SourceAccountRepository.upsert_by_external`（source_accounts.py:11）、`BaseRepository`（base.py:5）、`get_db`（session.py:41）、`celery_app`（celery_app.py:5）、`CreatorRepository.get_by_name`（creators.py:16）——零平行建设。
2. 最小集：12 任务各对应 RAD-020~023 或 C8 挂账，无可再砍项。
3. 复杂度检查（8+ 文件触发）：每个新文件 1:1 对应一个契约/职责（contracts/url_guard/adapter/factory/discovery/repo/api/tasks），合并违反 C3 分层与 KISS；判定按计划推进（P2/P5）。
4. 搜索检查：subprocess+timeout=stdlib [Layer 1]；yt-dlp CLI 即工具本身；无自造基础设施。
5. TODOS.md 不存在（挂账项记录于本计划 Review record 与 memory，接受）。
6. 完整性：五类 fixture + 全错误路径 + 幂等/并发靠唯一键，完整版。
7. 分发：镜像 yt-dlp 由 CI docker-build 作业验证，无缺口。

### Section 1: Architecture（依赖图）

```
apps/api 包内新增依赖（→ = import）
app/api/v1/source_items.py ──► services/discovery.py ──► repositories/{source_items,source_accounts,creators}
        │                              │                        │
        ▼                              ▼                        ▼
services/media/factory.py ◄── worker/tasks.py            db/models/source.py（既有）
        │                              │
        ▼                              ▼
services/media/adapters/yt_dlp.py ──► services/media/contracts.py ◄── services/media/url_guard
        │
        ▼
   subprocess → yt-dlp CLI（镜像内钉版本）
worker/tasks.py ──► worker/celery_app.py（beat + send_task 按名投递 prepare_source_item → EPIC-03）
```
- 无环；worker 不依赖 app.api（F1 已修）；contracts 零 ORM/平台依赖（C3）。
- 生产失败场景逐点：yt-dlp 非零→AdapterProcessError→502/任务计数 ✓；Redis 挂→beat/worker 死（既有基础设施边界，数据无损）✓；PG 挂于任务中→异常重抛、failure_count 写入也失败→任务标记失败无脏数据 ✓；平台改版→计数累积+每次派发重试（可接受，EPIC-10 加告警）。
- 状态机：本计划只引入 status='discovered' 单态，无非法迁移。
- 回滚：git revert，零迁移。零问题（CEO 已覆盖项不重复）。

### Section 2: Code Quality（发现 6，全部带证据引用）

1. **[P1] (9/10) E3 channel_url 死键**：计划 `discovery.create_item_from_url` 写 `url=media.metadata.get("channel_url")`（Task 9 代码），但 `_parse_resolved` 的 metadata 白名单只有 `("view_count","like_count","language","description")`（Task 6 代码）——channel_url 永远取不到，账号 url 恒 None。修复：ResolvedMedia 增显式字段 `channel_url: str | None = None`（默认值兼容既有测试构造），_parse_resolved 填充，discovery 改用。已采纳（P1 完整）。
2. [P2] (9/10) E1 Task 5 测试 helper 手工倒腾 os.environ 且调 `.run(`（实现名 run_json），与 Task 6/7 的 monkeypatch 风格分裂——统一 monkeypatch + run_json，顺带加 GAP-1。已采纳（P5 一致性）。
3. [P3] (8/10) E2 `model_config = {"from_attributes": True}` dict 字面量 → `ConfigDict(from_attributes=True)`（与 settings.py 风格一致）。已采纳。
4. [P3] (8/10) E4 集成测试 `from tests.unit.test_ytdlp_adapter_resolve import SUCCESS_PAYLOAD` 跨测试模块导入易碎——载荷常量移 `tests/fixtures/media/payloads.py` 共享。已采纳。
5. [P2] (8/10) E5 `dispatch_due_discoveries` 括号建议“下沉 list_due”改为规范要求（repo 可测 + 三态单测）。已采纳。
6. [P3] (7/10) E6 Task 4 Step 4.2 同屏出现两种实现（property/computed_field/field_validator 三段探索文字），实现者会困惑——定稿唯一实现：tuple 字段 + field_validator(mode="before")。已采纳（P5 显式）。

### Section 3: Test Review（覆盖图）

```
CODE PATHS                                              测试                        质量
[+] contracts.py   默认值/Protocol/错误层级            test_media_contracts       ★★
[+] url_guard      11 参数化（允许/拒绝/消息）          test_media_url_guard       ★★★
[+] settings       默认/CSV/空项                       test_settings_media        ★★★
[+] YtDlpProcess   成功/非零/坏URL/超时/坏JSON          test_ytdlp_process         ★★★
    └── GAP-1 二进制缺失 FileNotFoundError → 已并入 Step 5.2 新测试（match YTDLP_BINARY）
[+] resolve        成功/无字幕/live/缺字段/坏host/私有/超时  test_ytdlp_adapter_resolve ★★★
    └── GAP-2 published_at timestamp 分支 → 已并入 Step 6.2b
[+] discover       条目/空/无URL/坏URL                  test_ytdlp_adapter_discover ★★★
[+] item upsert    建/更/幂等/新建标志（真库）          test_source_item_repo      ★★★
[+] discovery svc  预览不落库/创建/幂等/回退/账号发现/失败计数  test_discovery_service ★★★
[+] API            200/422/400/502 (+GAP-3 504)        test_api_source_items      ★★★
    └── 集成 create 201/幂等                            test_api_create_source_item ★★
[+] worker 任务    e2e/due 三态/失败 (+GAP-4 beat 断言)  test_discover_tasks        ★★★
USER FLOWS
[+] 双击重复 POST → 幂等 upsert 断言 ✓；并发 discover 双投 → F4 契约注记（EPIC-03 幂等义务）✓
LLM/EVAL：本计划无 LLM 调用，无 eval 需求。
覆盖：计划内代码路径 22/22 有测试（GAP-1~4 采纳后），质量 ★★★ 15 / ★★ 4 / ★ 0
```
REGRESSION RULE：无既有行为被改（Task 1 为纯增强/回归锁定，get_db 测试即回归锁定）。测试计划工件已写盘（见下）。

### Section 4: Performance

- N+1：无读路径；discover 循环 upsert ≤50 行/账号轮询——可接受，不加批量（YAGNI，EPIC-03 若量涨再 batch）。
- 内存：playlist 封顶 50、resolve 单 JSON，有界。
- 并发：sync 端点跑 FastAPI threadpool（默认 40）限界子进程并发；worker 每 due 账号一任务。连接池沿用 5+10。
- 缓存：管理员工具，不加。0 issues。

### Failure Modes Registry（Eng 汇总）

| 代码路径 | 失败模式 | 测试? | 处理? | 用户可见? |
|---|---|---|---|---|
| run_json 二进制缺失 | FileNotFoundError | Y(GAP-1) | Y→AdapterProcessError | Y(502+指引) |
| run_json 非零/坏JSON/超时 | 三类异常 | Y | Y | Y(502/504) |
| resolve 坏host | 白名单拒绝 | Y | Y | Y(400) |
| discover 账号无URL | AdapterError | Y | Y | 任务失败可见 |
| 任务任何异常 | 计数+日志+重抛 | Y | Y(F2/F6) | 任务失败+日志 |
| send_task 后 Redis 挂 | 条目滞留 | N(G1) | 部分(日志+EPIC-03 补扫) | 延迟（非静默） |
| upsert 并发 | 唯一键冲突 | Y(Plan#1 约束测试) | Y(ON CONFLICT) | 无感 |

**CRITICAL GAP：0**（G1 有日志+兜底计划，非静默）。

### NOT in scope（Eng 维度）

- API 列表/查询端点（EPIC-07）；鉴权/限流（EPIC-07/10）；metrics/告警（EPIC-10）；outbox（明确不做）；批量 upsert（量涨再说）。

### What already exists（Eng 维度）

同 Step 0.1 清单；另有 tests/integration/conftest.py 的 radar_test 建库/TRUNCATE 隔离设施与 D39 并发 upsert 先例（Account 级，Item 级同构复用唯一键思路）。

### Worktree 并行策略

Sequential implementation, no parallelization opportunity——用户既定指令：不派 subagent、主会话串行（Task 1 独立可先行的并行性存在但按指令放弃）。

### Decision Audit Trail（eng）

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|----------|
| E-1 | eng | E3 channel_url 显式字段修复死键 | Mechanical | P1 | metadata 白名单无此键，现网必现 | 保持死键 |
| E-2 | eng | E1 helper 统一 monkeypatch+run_json | Mechanical | P5 | 风格分裂是坑 | 保留 os.environ |
| E-3 | eng | E2 ConfigDict | Mechanical | P5 | 与 settings 一致 | dict 字面量 |
| E-4 | eng | E4 共享载荷模块 | Mechanical | P4 | 跨测试导入易碎 | 跨模块 import |
| E-5 | eng | E5 list_due 下沉为规范 | Mechanical | P1/P5 | 可测性 | 任务内 text() |
| E-6 | eng | E6 settings 定稿唯一实现 | Mechanical | P5 | 三段探索文字致歧义 | 保留二选一 |
| E-7 | eng | GAP-1/2/3/4 补四条测试 | Mechanical | P1 | 覆盖 22/22 | 留缺口 |
| E-8 | eng | 批量 upsert 不做 | Taste(轻) | YAGNI | ≤50 行/轮 | 现在批量 |

<!-- autoplan-baseline-edits:eng {"sourceSha256":"2923be577b0e3ee72841e9eca09e4e6d3fd81156ea8a505e43e781fc03d9e17e","replacements":[{"oldText":"def run(behavior: str, payload: dict | None = None, timeout: int = 10):\n    import os\n    old = os.environ.get(\"FAKE_YTDLP_BEHAVIOR\")\n    os.environ[\"FAKE_YTDLP_BEHAVIOR\"] = behavior\n    if payload is not None:\n        os.environ[\"FAKE_YTDLP_PAYLOAD\"] = json.dumps(payload)\n    try:\n        return YtDlpProcess(binary=FAKE, timeout_sec=timeout).run([\"--dump-single-json\", \"https://www.youtube.com/watch?v=x\"])\n    finally:\n        os.environ.pop(\"FAKE_YTDLP_BEHAVIOR\", None)\n        if old is not None:\n            os.environ[\"FAKE_YTDLP_BEHAVIOR\"] = old\n        os.environ.pop(\"FAKE_YTDLP_PAYLOAD\", None)\n\n\ndef test_run_returns_parsed_json() -> None:\n    assert run(\"success\", {\"id\": \"abc\"}) == {\"id\": \"abc\"}\n\n\ndef test_nonzero_exit_raises_with_stderr_tail() -> None:\n    with pytest.raises(AdapterProcessError, match=\"private\"):\n        run(\"private\")\n\n\ndef test_unsupported_url_raises() -> None:\n    with pytest.raises(AdapterProcessError, match=\"Unsupported URL\"):\n        run(\"badurl\")\n\n\ndef test_timeout_kills_process() -> None:\n    with pytest.raises(AdapterTimeoutError):\n        run(\"timeout\", timeout=1)\n\n\ndef test_unparseable_stdout_raises() -> None:\n    with pytest.raises(AdapterProcessError, match=\"无法解析\"):\n        run(\"badjson\")","newText":"def run(monkeypatch: pytest.MonkeyPatch, behavior: str, payload: dict | None = None, timeout: int = 10):\n    # E1：统一 monkeypatch（与 Task 6/7 同款），失败也自动还原，杜绝 env 串扰\n    monkeypatch.setenv(\"FAKE_YTDLP_BEHAVIOR\", behavior)\n    if payload is not None:\n        monkeypatch.setenv(\"FAKE_YTDLP_PAYLOAD\", json.dumps(payload))\n    return YtDlpProcess(binary=FAKE, timeout_sec=timeout).run_json(\n        [\"--dump-single-json\", \"https://www.youtube.com/watch?v=x\"]\n    )\n\n\ndef test_run_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:\n    assert run(monkeypatch, \"success\", {\"id\": \"abc\"}) == {\"id\": \"abc\"}\n\n\ndef test_nonzero_exit_raises_with_stderr_tail(monkeypatch: pytest.MonkeyPatch) -> None:\n    with pytest.raises(AdapterProcessError, match=\"private\"):\n        run(monkeypatch, \"private\")\n\n\ndef test_unsupported_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:\n    with pytest.raises(AdapterProcessError, match=\"Unsupported URL\"):\n        run(monkeypatch, \"badurl\")\n\n\ndef test_timeout_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:\n    with pytest.raises(AdapterTimeoutError):\n        run(monkeypatch, \"timeout\", timeout=1)\n\n\ndef test_unparseable_stdout_raises(monkeypatch: pytest.MonkeyPatch) -> None:\n    with pytest.raises(AdapterProcessError, match=\"无法解析\"):\n        run(monkeypatch, \"badjson\")\n\n\ndef test_missing_binary_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:\n    # GAP-1：二进制缺失 → AdapterProcessError 且消息含 YTDLP_BINARY 指引\n    proc = YtDlpProcess(binary=\"/nonexistent/yt-dlp\", timeout_sec=5)\n    with pytest.raises(AdapterProcessError, match=\"YTDLP_BINARY\"):\n        proc.run_json([\"--dump-single-json\", \"https://www.youtube.com/watch?v=x\"])"},{"oldText":"（实现后若 fixture env 污染引发串扰，可改用 monkeypatch.setenv 传 env——保持测试独立。）","newText":"（E1：helper 已统一 monkeypatch；方法名统一 `run_json`。）"},{"oldText":"    subtitles: tuple[SubtitleTrack, ...]\n    metadata: dict\n\n\n@dataclass(frozen=True)\nclass SubtitleResult:","newText":"    subtitles: tuple[SubtitleTrack, ...]\n    metadata: dict\n    channel_url: str | None = None  # E3：频道页 URL（账号 upsert 用），默认 None 兼容测试构造\n\n\n@dataclass(frozen=True)\nclass SubtitleResult:"},{"oldText":"        subtitles=_parse_subtitles(data),\n        metadata={k: data[k] for k in (\"view_count\", \"like_count\", \"language\", \"description\") if k in data},","newText":"        subtitles=_parse_subtitles(data),\n        metadata={k: data[k] for k in (\"view_count\", \"like_count\", \"language\", \"description\") if k in data},\n        channel_url=data.get(\"channel_url\"),  # E3"},{"oldText":"        url=media.metadata.get(\"channel_url\"),","newText":"        url=media.channel_url,  # E3：显式字段（metadata 快照里从未放过 channel_url）"},{"oldText":"    model_config = {\"from_attributes\": True}","newText":"    model_config = ConfigDict(from_attributes=True)  # E2：显式 ConfigDict，与 settings.py 风格一致"},{"oldText":"from pydantic import BaseModel, HttpUrl","newText":"from pydantic import BaseModel, ConfigDict, HttpUrl"},{"oldText":"from tests.unit.test_ytdlp_adapter_resolve import SUCCESS_PAYLOAD  # 复用载荷常量","newText":"from tests.fixtures.media.payloads import SUCCESS_PAYLOAD  # E4：共享载荷模块，避免跨测试模块导入"},{"oldText":"SUCCESS_PAYLOAD = {","newText":"# E4：此常量落 tests/fixtures/media/payloads.py，供 unit/integration 共同 import\nSUCCESS_PAYLOAD = {"},{"oldText":"（`dispatch_due_discoveries` 的到期判定建议下沉为 `SourceAccountRepository.list_due(now)`，任务层只做派发——repo 一条可测方法 + 任务薄壳。）","newText":"（E5：到期判定**必须**下沉为 `SourceAccountRepository.list_due(now)`——repo 一条可测方法（含 due/ enabled/disabled 三态单测）+ 任务薄壳。）"},{"oldText":"    ytdlp_timeout_sec: int = 60\n    media_host_allowlist: str = \"youtube.com,youtu.be,bilibili.com,douyin.com\"\n    discover_playlist_max_items: int = 50","newText":"    ytdlp_timeout_sec: int = 60\n    discover_playlist_max_items: int = 50"},{"oldText":"（属性化解析放 `@computed_field` 或 `@property`；用 property：\n\n```python\n    @property\n    def media_host_allowlist_tuple(self) -> tuple[str, ...]:\n        return tuple(h.strip().lower() for h in self.media_host_allowlist.split(\",\") if h.strip())\n```\n\n——注意测试断言的是 `s.media_host_allowlist == (...)` 元组。二选一：**改为 computed_field 返回 tuple，字段名 media_host_allowlist 输入为 str**。pydantic computed_field 与同名输入冲突，所以直接用 `field_validator(mode=\"before\")` 把 str 规整为 tuple 存进字段：","newText":"（E6 定稿——唯一实现：字段声明 tuple 默认值，`field_validator(mode=\"before\")` 把环境变量 CSV 解析为 tuple，env 名自动映射 `MEDIA_HOST_ALLOWLIST`："},{"oldText":"- [ ] **Step 6.3 测试通过 → 提交** `git commit -m \"feat: GenericYtDlpAdapter.resolve 统一元数据解析（RAD-021）\"`","newText":"- [ ] **Step 6.2b 补充边界测试（Eng GAP-2）**：`_parse_published_at` 的 timestamp 分支（upload_date 分支已被 SUCCESS_PAYLOAD 覆盖）：\n\n```python\ndef test_resolve_published_at_from_unix_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:\n    payload = {**SUCCESS_PAYLOAD, \"timestamp\": 1773792000, \"upload_date\": None}\n    media = make_adapter(monkeypatch, \"success\", payload).resolve(URL)\n    assert media.published_at == datetime.fromtimestamp(1773792000, tz=UTC)\n```\n\n- [ ] **Step 6.3 测试通过 → 提交** `git commit -m \"feat: GenericYtDlpAdapter.resolve 统一元数据解析（RAD-021）\"`"},{"oldText":"def test_resolve_url_upstream_failure_502(client, monkeypatch):\n    with _install(monkeypatch, StubAdapter(AdapterProcessError(\"yt-dlp 退出码 1: private\"))):\n        resp = client.post(\"/api/v1/source-items/resolve-url\", json={\"url\": \"https://www.youtube.com/watch?v=x\"})\n    assert resp.status_code == 502","newText":"def test_resolve_url_upstream_failure_502(client, monkeypatch):\n    with _install(monkeypatch, StubAdapter(AdapterProcessError(\"yt-dlp 退出码 1: private\"))):\n        resp = client.post(\"/api/v1/source-items/resolve-url\", json={\"url\": \"https://www.youtube.com/watch?v=x\"})\n    assert resp.status_code == 502\n\n\ndef test_resolve_url_timeout_504(client, monkeypatch):  # GAP-3\n    with _install(monkeypatch, StubAdapter(AdapterTimeoutError(\"yt-dlp 超时（>60s）\"))):\n        resp = client.post(\"/api/v1/source-items/resolve-url\", json={\"url\": \"https://www.youtube.com/watch?v=x\"})\n    assert resp.status_code == 504"},{"oldText":"- [ ] **Step 11.4 EPIC-03 契约注记（F4）**","newText":"- [ ] **Step 11.3b beat 配置断言（Eng GAP-4）**：`tests/integration/test_discover_tasks.py` 追加——\n\n```python\ndef test_beat_schedule_wired():\n    from app.worker.celery_app import celery_app\n    sched = celery_app.conf.beat_schedule.get(\"dispatch-due-discoveries\")\n    assert sched is not None and sched[\"task\"] == \"dispatch_due_discoveries\"\n    assert sched[\"schedule\"] > 0\n```\n\n- [ ] **Step 11.4 EPIC-03 契约注记（F4）**"}]} -->
<!-- autoplan-accepted:eng -->
- E3: `ResolvedMedia` 增 `channel_url: str | None = None` 字段；`_parse_resolved` 填 `data.get("channel_url")`；`create_item_from_url` 用 `media.channel_url`；验证=test_discovery_service 断言 account.url 为 stub 提供的 channel_url（SUCCESS_PAYLOAD 加 channel_url 键）
- E1: Task 5 测试 helper 改 monkeypatch 签名 + `run_json` 方法名；五个用例签名同步；验证=test_ytdlp_process 全绿且无 os.environ 直接操作
- E2/E4/E5/E6: ConfigDict、payloads 共享模块、list_due 下沉（含三态单测）、settings 唯一实现——分别以对应测试/代码规范落地；验证=make lint + mypy + 全量 pytest
- GAP-1: `test_missing_binary_raises_with_hint`（match="YTDLP_BINARY"）入 test_ytdlp_process
- GAP-2: `test_resolve_published_at_from_unix_timestamp` 入 test_ytdlp_adapter_resolve
- GAP-3: `test_resolve_url_timeout_504` 入 test_api_source_items
- GAP-4: `test_beat_schedule_wired` 入 test_discover_tasks
<!-- /autoplan-accepted:eng -->

ENG DUAL VOICES — CONSENSUS TABLE:
```
  Dimension                           Claude(in-host)  Codex   Consensus
  ──────────────────────────────────── ──────────────── ─────── ─────────
  1. Architecture sound?               是               N/A     N/A
  2. Test coverage sufficient?         是(22/22)        N/A     N/A
  3. Performance risks addressed?      是               N/A     N/A
  4. Security threats covered?         是(CEO F3)       N/A     N/A
  5. Error paths handled?              是(0 静默)       N/A     N/A
  6. Deployment risk manageable?       是(零迁移)       N/A     N/A
```
外审不可用（codex model_unusable；subagent 按用户指令不派发）→ 共识格 N/A，永不 CONFIRMED。

### Completion Summary

- Step 0: Scope Challenge — scope accepted as-is（复杂度触发已答复：文件数=契约映射，非过度设计）
- Architecture Review: 0 new issues（CEO 已修 F1 复核通过：worker 不依赖 api）
- Code Quality Review: 6 issues found（E1-E6 全部采纳）
- Test Review: diagram produced, 4 gaps identified（GAP-1~4 全部补入计划）
- Performance Review: 0 issues
- NOT in scope: written；What already exists: written
- TODOS.md updates: 0 新增（延后项均挂后续 EPIC 并已列 CEO/DX 记录）
- Failure modes: 0 critical gaps
- Outside voice: unavailable（codex model_unusable；单通道 in-host + 对抗自查）
- Parallelization: sequential（用户指令）
- Lake Score: 8/8（全部完整选项）
- Unresolved decisions: 0（eng 内；跨阶段 3 taste 挂大门）

### Implementation Tasks（eng 阶段）

全部采纳项已作为 baseline edits 并入既有 Task 5/6/9/10/11 的步骤与测试，无独立新任务文件；聚合 JSONL 见 ~/.gstack/projects/FinanceOpinionRadar/tasks-eng-review-*.jsonl。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` via /autoplan | Scope & strategy | 1 | CLEAR (SELECTIVE_EXPANSION) | 5 前提成立；1 proposal/0 accepted；0 critical gaps |
| Outside Review | codex via /autoplan | Independent 2nd opinion | 3 phases | unavailable | model_unusable（CLI 无法解码账号模型列表）；单通道 in-host + 对抗自查 |
| Eng Review | `/plan-eng-review` via /autoplan | Architecture & tests (required) | 1 | CLEAR (FULL_REVIEW) | 6 issues + 4 test gaps 全采纳；22/22 路径有测试；0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | skipped | 无 UI 范围（关键词命中均为 platform 词尾假阳性） |
| DX Review | `/plan-devex-review` via /autoplan | Developer experience gaps | 1 | CLEAR (DX_POLISH) | 7→8/10；TTHW 5min Competitive；1 采纳（DX1） |

- **OUTSIDE COVERAGE:** codex, phases ceo/dx/eng = unavailable (model_unusable；修复 `export GSTACK_CODEX_MODEL=<可用模型>`)；design = skipped（无 UI 范围）。全程无外部模型第二意见；覆盖为部分。
- **VERDICT:** CEO + ENG + DX CLEARED — 批准实施（用户 2026-09-17 选 A：接受全部推荐，含 T1/T2/T3）。
- **实施方式:** 主会话逐任务串行（用户既定指令，不派 subagent）。

NO UNRESOLVED DECISIONS
