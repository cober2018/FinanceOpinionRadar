<!-- /autoplan restore point: "/Users/mshengran/.gstack/projects/FinanceOpinionRadar/main-autoplan-restore-20260917-080610.md" -->
## Implementation plan
# EPIC-03 媒体、字幕与 ASR 实施计划（Plan #3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **本仓库既定约定（沿 Plan #1/#2）**：用户要求不派 subagent，主会话逐任务串行执行；单分支 main 直推；提交规范 `feat|fix|refactor|docs|chore`。

**Goal:** 一个 URL（抖音短视频/直播回放为主，YouTube 次之）能自动变成带时间戳的 transcript：`prepare_source_item` 编排"resolve 补全 → 字幕优先 → 无字幕则下载音频 → FFmpeg 标准化 → faster-whisper ASR → transcript_segment 落库"。

**Architecture:** 延续 ADR-0001/0002/0007：storage（boto3/MinIO）、audio（FFmpeg 子进程）、transcription（faster-whisper 进程内）三个服务模块挂在 `app/services/` 下；`prepare_source_item` 是 Celery 任务，按 PRD 状态机推进 `source_item.status`，行锁保证幂等；beat 周期补扫 `discovered` 存量。WhisperX 只做 feature flag + 薄 Provider，V1 不依赖。

**Tech Stack:** boto3（S3/MinIO）、ffmpeg 子进程、faster-whisper（ctranslate2）、whisperx（optional extra，默认不装）、pytest（unit + integration，真 PG `radar_test`）。

---

## 0. 背景与已定决策

### 产品形态（用户 2026-09-17 确认）

- **主平台抖音**，入口以**单视频链接**（含 `v.douyin.com` 短链）为主；内容形态 = **短视频 + 直播回放**（回放即普通 VOD，走同一条管线）。
- **PRD 明确 V1 只做 VOD**：实时直播值守（录制）是 V1.5（DouyinLiveRecorder 独立进程，PRD L51/L210/§14）。本计划不碰直播录制。
- **yt-dlp 能力边界（实测 2026.03.17）**：`douyin.com/user/…` 主页 URL → `Unsupported URL`，即 **yt-dlp 无抖音用户页列表能力** → 抖音在 V1 无法账号定时发现，靠手工贴链接；YouTube 账号发现保留（EPIC-02 已验证）。

### 执行计划注记裁决（2026-09-17，D1 对话）

- **① prepare_source_item 幂等**：`SELECT … FOR UPDATE` 行锁 + 状态门槛（仅 `discovered`/`failed` 可进入），重复投递安全。
- **② 存量补扫**：beat 周期任务 `dispatch_pending_prepares` 扫 `status='discovered'` 派发（举一反三：G1"commit 后 send 失败滞留"被永久兜底，不只一次性补扫）。
- **③ 裸频道 URL**：选 **A 注册时规范化**——`normalize_channel_url`（仅 YouTube：`/channel/UC…`、`/@handle`、`/c/…`、`/user/…` 裸地址追加 `/videos`）+ 一条幂等 Alembic 数据迁移补存量。抖音侧不适用（无主页列表能力，见上）。
- **④ resolve 拒收非单条 URL**：`_parse_resolved` 见 `_type='playlist'` 或 `entries` 即抛 `NotSingleItemError` → API 映射 400，提前失败不吃 60s 超时。

### 关键契约决定

- `SubtitleResult` 充实：`language, content: bytes, fmt: str("json3"|"vtt"), auto: bool`（新字段带默认值，兼容旧构造）。
- `fetch_subtitle(item, language=None, *, auto=False)`：service 层按偏好选语言，adapter 只抓指定轨道（manual 用 `--write-subs`，auto 用 `--write-auto-subs`）。
- `download_media(item, workdir)`：workdir 由编排层持有（`TemporaryDirectory` 生命周期归编排层，adapter 不泄漏临时目录）。
- transcript 身份记录进 `source_item.metadata_json["transcript"]`（provider/model/language/media_sha256/segment_count），为 EPIC-04 EXTRACT 幂等键（含 `transcript_revision` 概念）留桩；TRANSCRIBE 幂等键 `media_sha256 + asr_provider + model_version` 即由此字段集承载。
- 原始 provider output JSON 上传对象存储，落 `media_asset(asset_type='transcript')`（PRD 注释列 video/audio/subtitle，扩展值不做迁移，docstring 注明）。
- 错误码：FFmpeg 失败用 PRD 原文 `MEDIA_FFMPEG_FAILED`；其余阶段码 `RESOLVE_FAILED / DOWNLOAD_FAILED / ASR_FAILED / TRANSCRIPT_FAILED`，连同 stage/时间记 `source_item.metadata_json["last_error"]`。

### 文件结构（新增）

```text
apps/api/app/domain/pipeline_states.py          # 状态机 guard（PRD §11）
apps/api/app/services/storage/__init__.py      # get_storage 工厂
apps/api/app/services/storage/base.py          # Storage Protocol + StorageError
apps/api/app/services/storage/s3.py            # MinioStorage（boto3）
apps/api/app/services/media/audio.py           # FFmpeg 标准化（RAD-032）
apps/api/app/services/media/subtitles.py       # json3/vtt 解析 + 可用性判定
apps/api/app/services/media/adapters/yt_dlp.py # +fetch_subtitle/download_media/normalize/拒收
apps/api/app/services/transcription/__init__.py        # get_transcription_provider 工厂（flag 分支）
apps/api/app/services/transcription/contracts.py       # TranscriptResult/Provider Protocol
apps/api/app/services/transcription/faster_whisper.py  # FasterWhisperProvider（RAD-033）
apps/api/app/services/transcription/whisperx_provider.py # 薄实现，默认不启用（RAD-035）
apps/api/app/services/preparation.py           # prepare_source_item 编排（RAD-031 核心）
apps/api/app/repositories/media_assets.py      # media_asset 仓储
apps/api/app/repositories/transcripts.py       # transcript_segment 仓储（replace 语义）
apps/api/migrations/versions/xxxx_backfill_youtube_channel_urls.py  # 注记③存量补扫
```

修改：`contracts.py`、`url_guard.py` 不动；`discovery.py`（注册侧 normalize）、`worker/tasks.py`、`worker/celery_app.py`（beat）、`api/v1/source_items.py`（400 映射）、`core/settings.py`、`apps/api/pyproject.toml`、`infra/docker/Dockerfile.api`、`.env.example`、`README.md`。

### Settings 新增（全部有默认值，dev 开箱可用）

```python
# --- EPIC-03 媒体与 ASR ---
ytdlp_download_timeout_sec: int = 600          # 下载/取字幕比 resolve 慢得多
ffmpeg_binary: str = "ffmpeg"
ffmpeg_timeout_sec: int = 600
subtitle_lang_preference: tuple[str, ...] = ("zh-Hans", "zh", "en")   # 依次前缀匹配
subtitle_min_chars: int = 10                   # 解析后总字符数低于此视为不可用 → 走 ASR
asr_model_name: str = "small"
asr_device: str = "cpu"
asr_compute_type: str = "int8"
asr_beam_size: int = 5
enable_whisperx: bool = False                  # RAD-035：默认关，V1 不依赖
enable_diarization: bool = False
prepare_sweep_interval_sec: int = 600          # 注记②补扫周期
prepare_sweep_batch_size: int = 200
transcript_overlap_tolerance_ms: int = 2000    # RAD-034 重叠阈值
prepare_max_media_duration_sec: int = 14400    # CEO-2C：直播回放可达数小时，超限快速失败防 CPU 长期占用
```

（`subtitle_lang_preference` 复用 `_parse_allowlist` 同款 CSV validator 模式。）

---

## Task 1: Pipeline 状态机 guard

**Files:**
- Create: `apps/api/app/domain/pipeline_states.py`
- Test: `tests/unit/test_pipeline_states.py`

- [ ] **Step 1: 写失败测试**

```python
from app.domain.pipeline_states import PIPELINE_TRANSITIONS, ensure_transition, InvalidTransitionError
import pytest

def test_happy_path_chain():
    chain = ["discovered", "resolved", "media_ready", "transcribing", "transcribed"]
    for cur, nxt in zip(chain, chain[1:]):
        ensure_transition(cur, nxt)  # 不抛

def test_prepare_retry_from_failed():
    ensure_transition("failed", "resolved")

def test_skip_forward_rejected():
    with pytest.raises(InvalidTransitionError):
        ensure_transition("discovered", "transcribed")

def test_ready_is_terminal():
    with pytest.raises(InvalidTransitionError):
        ensure_transition("ready", "failed")

def test_all_prd_states_present():
    assert set(PIPELINE_TRANSITIONS) == {
        "discovered", "resolved", "media_ready", "transcribing", "transcribed",
        "extracting", "reviewing", "ready", "failed", "ignored",
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_pipeline_states.py -v`
Expected: FAIL `ModuleNotFoundError: app.domain.pipeline_states`

- [ ] **Step 3: 最小实现**

```python
"""source_item.status 状态机（PRD §11）：只允许声明的迁移，防前端/任务乱改字符串。"""

PIPELINE_TRANSITIONS: dict[str, set[str]] = {
    "discovered": {"resolved", "failed", "ignored"},
    "resolved": {"media_ready", "failed", "ignored"},
    "media_ready": {"transcribing", "failed", "ignored"},
    "transcribing": {"transcribed", "failed"},
    "transcribed": {"extracting", "failed"},  # EPIC-04 起
    "extracting": {"reviewing", "failed"},
    "reviewing": {"ready", "failed"},
    "ready": set(),
    "failed": {"resolved", "ignored"},  # retry 从 resolved 重跑 prepare
    "ignored": set(),
}


class InvalidTransitionError(ValueError):
    pass


def ensure_transition(current: str, new: str) -> None:
    allowed = PIPELINE_TRANSITIONS.get(current)
    if allowed is None:
        raise InvalidTransitionError(f"未知状态: {current!r}")
    if new not in allowed:
        raise InvalidTransitionError(f"不允许 {current!r} → {new!r}（允许: {sorted(allowed)}）")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/unit/test_pipeline_states.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/domain/pipeline_states.py tests/unit/test_pipeline_states.py
git commit -m "feat: pipeline 状态机 guard（PRD §11）"
```

---

## Task 2: Storage 服务（RAD-030）

**Files:**
- Create: `apps/api/app/services/storage/__init__.py`、`base.py`、`s3.py`
- Modify: `apps/api/pyproject.toml`（+`boto3>=1.34`）
- Test: `tests/unit/test_storage_s3.py`、`tests/integration/test_storage_minio.py`

接口钉死：`put_file/get_signed_url/exists/delete`。业务代码禁止直接 import boto3（ruff 无法强约束，靠 review + docstring 声明）。

- [ ] **Step 1: pyproject 加依赖并安装**

`dependencies` 增加 `"boto3>=1.34"`，然后 `.venv/bin/pip install -e apps/api`。

- [ ] **Step 2: 写失败测试（stub client 注入）**

```python
# tests/unit/test_storage_s3.py —— 不碰网络：注入假 botocore client
import botocore.exceptions
import pytest
from app.services.storage.s3 import MinioStorage, StorageError

class FakeClient:
    def __init__(self, head_error=None):
        self.calls = []
        self._head_error = head_error
    def head_bucket(self, Bucket): 
        self.calls.append(("head_bucket", Bucket))
        if self._head_error: raise self._head_error
    def create_bucket(self, Bucket): self.calls.append(("create_bucket", Bucket))
    def upload_file(self, Filename, Bucket, Key, **kw): self.calls.append(("upload_file", Key))
    def head_object(self, Bucket, Key):
        self.calls.append(("head_object", Key))
        if Key == "missing": raise botocore.exceptions.ClientError({"Error": {"Code": "404"}}, "HeadObject")
    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn): return f"https://sig/{Params['Key']}"
    def delete_object(self, Bucket, Key): self.calls.append(("delete_object", Key))

def make(client): return MinioStorage(endpoint="http://localhost:9000", access_key="k", secret_key="s", bucket="b", _client=client)

def test_bucket_ensured_once_then_reused(tmp_path):
    c = FakeClient()
    s = make(c)
    f = tmp_path / "a.bin"; f.write_bytes(b"x")
    s.put_file("a/b.bin", f); s.put_file("c.bin", f)
    assert c.calls.count(("head_bucket", "b")) == 1
    assert c.calls.count(("create_bucket", "b")) == 0

def test_bucket_created_when_missing(tmp_path):
    c = FakeClient(head_error=botocore.exceptions.ClientError({"Error": {"Code": "404"}}, "HeadBucket"))
    s = make(c)
    s.put_file("k", tmp_path / "a.bin")
    assert ("create_bucket", "b") in c.calls

def test_put_returns_s3_uri(tmp_path):
    s = make(FakeClient())
    f = tmp_path / "a.bin"; f.write_bytes(b"x")
    assert s.put_file("sub/a", f) == "s3://b/sub/a"

def test_exists_true_false():
    s = make(FakeClient())
    assert s.exists("k") is True
    assert s.exists("missing") is False

def test_signed_url_and_delete():
    s = make(FakeClient())
    assert s.get_signed_url("k", expires_sec=60) == "https://sig/k"
    s.delete("k")  # 不抛

def test_boto3_errors_wrapped(tmp_path):
    class Boom(FakeClient):
        def upload_file(self, *a, **k): raise botocore.exceptions.BotoCoreError()
    with pytest.raises(StorageError):
        make(Boom()).put_file("k", tmp_path / "a.bin")
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_storage_s3.py -v`
Expected: FAIL ModuleNotFoundError

- [ ] **Step 4: 实现 base.py / s3.py / __init__.py**

```python
# base.py
"""对象存储契约（RAD-030）：业务代码只许用此接口，禁止直接调用 boto3。"""
from pathlib import Path
from typing import Protocol

class StorageError(Exception):
    """存储层错误统一包装（网络/权限/桶不存在等）。"""

class Storage(Protocol):
    def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        """上传本地文件，返回 storage_uri（s3://<bucket>/<key>）。"""
    def get_signed_url(self, key: str, *, expires_sec: int = 3600) -> str: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
```

```python
# s3.py
"""MinIO/S3 实现：path-style 寻址 + 惰性 ensure bucket。"""
from functools import partial
from pathlib import Path

import boto3
import botocore.exceptions
from botocore.config import Config

from app.services.storage.base import StorageError


class MinioStorage:
    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, bucket: str, _client=None) -> None:
        self._bucket = bucket
        self._client = _client  # 测试注入
        self._new_client = partial(
            boto3.client, "s3", endpoint_url=endpoint,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}, retries={"max_attempts": 2}),
        )
        self._bucket_ready = False

    def _s3(self):
        if self._client is None:
            self._client = self._new_client()
        return self._client

    def _ensure_bucket(self) -> None:
        if self._bucket_ready: return
        try:
            self._s3().head_bucket(Bucket=self._bucket)
        except botocore.exceptions.ClientError:
            self._s3().create_bucket(Bucket=self._bucket)
        except botocore.exceptions.BotoCoreError as exc:
            raise StorageError(f"存储不可达: {exc}") from exc
        self._bucket_ready = True

    def put_file(self, key, path, *, content_type=None) -> str:
        self._ensure_bucket()
        extra = {"ContentType": content_type} if content_type else {}
        try:
            self._s3().upload_file(str(path), self._bucket, key, ExtraArgs=extra or None)
        except (botocore.exceptions.BotoCoreError, OSError) as exc:
            raise StorageError(f"上传失败 {key}: {exc}") from exc
        return f"s3://{self._bucket}/{key}"

    def get_signed_url(self, key, *, expires_sec=3600) -> str:
        self._ensure_bucket()
        try:
            return self._s3().generate_presigned_url(
                "get_object", {"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_sec)
        except botocore.exceptions.BotoCoreError as exc:
            raise StorageError(f"签名失败 {key}: {exc}") from exc

    def exists(self, key) -> bool:
        self._ensure_bucket()
        try:
            self._s3().head_object(Bucket=self._bucket, Key=key)
            return True
        except botocore.exceptions.ClientError:
            return False

    def delete(self, key) -> None:
        self._ensure_bucket()
        try:
            self._s3().delete_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.BotoCoreError as exc:
            raise StorageError(f"删除失败 {key}: {exc}") from exc
```

```python
# __init__.py
from functools import lru_cache

from app.core.settings import get_settings
from app.services.storage.s3 import MinioStorage


@lru_cache
def get_storage() -> MinioStorage:
    s = get_settings()
    return MinioStorage(
        endpoint=s.s3_endpoint_url, access_key=s.s3_access_key,
        secret_key=s.s3_secret_key, bucket=s.s3_bucket_media,
    )
```

- [ ] **Step 5: 跑单测确认通过**

Run: `.venv/bin/pytest tests/unit/test_storage_s3.py -v`
Expected: PASS

- [ ] **Step 6: 写 MinIO 集成测试（真实例，不可达即跳过）**

```python
# tests/integration/test_storage_minio.py
import socket
import pytest
from app.services.storage.s3 import MinioStorage
from app.core.settings import get_settings

@pytest.fixture(scope="module")
def storage():
    s = get_settings()
    host = s.s3_endpoint_url.split("//")[1].split(":")[0]
    port = int(s.s3_endpoint_url.rsplit(":", 1)[1])
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex((host, port)) != 0:
            pytest.skip(f"MinIO 不可达 {s.s3_endpoint_url}（先 make bootstrap）")
    return MinioStorage(endpoint=s.s3_endpoint_url, access_key=s.s3_access_key,
                         secret_key=s.s3_secret_key, bucket=f"{s.s3_bucket_media}-test")

def test_roundtrip(tmp_path, storage):
    key = f"it/{tmp_path.name}/a.txt"
    f = tmp_path / "a.txt"; f.write_text("hello")
    uri = storage.put_file(key, f)
    assert uri.startswith("s3://") and storage.exists(key)
    assert "a.txt" in storage.get_signed_url(key)
    storage.delete(key)
    assert not storage.exists(key)
```

- [ ] **Step 7: 跑集成测试（compose 栈在线时）**

Run: `DATABASE_URL="postgresql+psycopg://radar:radar@localhost:5544/radar" .venv/bin/pytest tests/integration/test_storage_minio.py -v`
Expected: PASS（MinIO 未起则 SKIP）

- [ ] **Step 8: 提交**

```bash
git add apps/api/app/services/storage/ apps/api/pyproject.toml tests/unit/test_storage_s3.py tests/integration/test_storage_minio.py
git commit -m "feat: storage 服务（MinIO/S3，RAD-030）"
```

---

## Task 3: Adapter 补全——④ playlist 拒收 + ③ 频道 URL 规范化

**Files:**
- Modify: `apps/api/app/services/media/contracts.py`（+`NotSingleItemError`）
- Modify: `apps/api/app/services/media/adapters/yt_dlp.py`（`_parse_resolved` 拒收 + `normalize_channel_url`）
- Modify: `apps/api/app/services/discovery.py`（注册侧 normalize）
- Modify: `apps/api/app/api/v1/source_items.py`（`NotSingleItemError` → 400）
- Test: `tests/unit/test_ytdlp_adapter_resolve.py`（追加）、`tests/unit/test_channel_url_normalize.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_channel_url_normalize.py
import pytest
from app.services.media.adapters.yt_dlp import normalize_channel_url

@pytest.mark.parametrize("raw,expected", [
    ("https://www.youtube.com/channel/UCuAXFkgcl1yQ6_0FhPI_DAA", "https://www.youtube.com/channel/UCuAXFkgcl1yQ6_0FhPI_DAA/videos"),
    ("https://www.youtube.com/@ValueInvesting/", "https://www.youtube.com/@ValueInvesting/videos"),
    ("https://www.youtube.com/c/SomeName", "https://www.youtube.com/c/SomeName/videos"),
    ("https://www.youtube.com/user/oldname", "https://www.youtube.com/user/oldname/videos"),
])
def test_bare_channel_appends_videos(raw, expected):
    assert normalize_channel_url(raw, platform="youtube") == expected

@pytest.mark.parametrize("already", [
    "https://www.youtube.com/@x/videos",
    "https://www.youtube.com/@x/streams",
    "https://www.youtube.com/@x/shorts",
    "https://www.youtube.com/watch?v=abc",
    "https://www.youtube.com/playlist?list=PL123",
])
def test_tab_or_content_urls_untouched(already):
    assert normalize_channel_url(already, platform="youtube") == already

def test_non_youtube_passthrough():
    assert normalize_channel_url("https://www.douyin.com/user/xyz", platform="douyin") == "https://www.douyin.com/user/xyz"

def test_none_passthrough():
    assert normalize_channel_url(None, platform="youtube") is None
```

resolve 拒收（追加进 `test_ytdlp_adapter_resolve.py`，沿用其现有 make/FAKE 载荷构造方式）：

```python
def test_resolve_rejects_playlist_payload(...):  # 载荷含 "_type":"playlist" / "entries"
    with pytest.raises(NotSingleItemError):
        adapter.resolve("https://www.youtube.com/watch?v=ok")  # FAKE 二进制返回 playlist 载荷
```

ENG-2A 补两条（断 API/编排接线，不止函数本体）：

```python
# test_api_source_items.py 追加 —— fake adapter 抛 NotSingleItemError → 400（不是 500/504）
def test_resolve_url_playlist_returns_400(...): ...

# test_upsert_refresh_semantics.py（或 discovery 集成测试）追加 —— 注册侧 normalize 接线生效
def test_manual_registration_normalizes_channel_url(...):  # channel_url 裸地址入库后 source_account.url 已带 /videos
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_channel_url_normalize.py tests/unit/test_ytdlp_adapter_resolve.py -v`
Expected: FAIL（`normalize_channel_url` 不存在 / `NotSingleItemError` 未导出）

- [ ] **Step 3: 实现**

contracts.py 追加：

```python
class NotSingleItemError(AdapterError):
    """resolve 目标不是单条内容（频道/播放列表页）——注记④，提前失败防 60s 超时。"""
```

yt_dlp.py：`_parse_resolved` 开头加

```python
    if data.get("_type") == "playlist" or data.get("entries") is not None:
        raise NotSingleItemError("非单条内容 URL（频道/播放列表），请提供具体视频地址")
```

模块级新增（imports 补 `re`）：

```python
# 注记③：注册时把 YouTube 裸频道地址规范化为可列表的 /videos 页签（ISSUE-003）
_YOUTUBE_CHANNEL_RE = re.compile(
    r"^(https?://[^/]*youtube\.com/(?:channel/UC[\w-]{20,}|@[\w.\-]+|c/[\w.\-]+|user/[\w.\-]+?))/?$"
)
_YOUTUBE_LISTABLE_SUFFIXES = ("/videos", "/streams", "/shorts", "/featured", "/playlists")


def normalize_channel_url(url: str | None, *, platform: str) -> str | None:
    if not url or platform != "youtube":
        return url
    if any(s in url for s in _YOUTUBE_LISTABLE_SUFFIXES) or "/watch" in url or "list=" in url:
        return url
    m = _YOUTUBE_CHANNEL_RE.match(url.strip().rstrip("/"))
    return f"{m.group(1)}/videos" if m else url
```

discovery.py `create_item_from_url` 账号 upsert 处：

```python
        url=normalize_channel_url(media.channel_url, platform=media.platform),  # 注记③
```

api/v1/source_items.py：异常映射处将 `NotSingleItemError` 与 `UrlNotAllowedError` 同样映射 HTTP 400（消息即"非单条内容 URL…"）。

- [ ] **Step 4: 跑测试确认通过（含旧测试回归）**

Run: `.venv/bin/pytest tests/unit/test_channel_url_normalize.py tests/unit/test_ytdlp_adapter_resolve.py tests/unit/test_api_source_items.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/media/ apps/api/app/services/discovery.py apps/api/app/api/v1/source_items.py tests/
git commit -m "feat: resolve 拒收 playlist 载荷 + YouTube 频道 URL 注册侧规范化（注记③④）"
```

---

## Task 4: 存量频道 URL 补扫迁移（注记③）

**Files:**
- Create: `apps/api/migrations/versions/<rev>_backfill_youtube_channel_urls.py`
- Test: `tests/integration/test_backfill_channel_urls.py`

规则与 Task 3 的 Python 侧一致，但迁移内联 SQL（Alembic 迁移不 import 应用代码，快照自洽）。幂等：谓词排除已带页签后缀的行。

- [ ] **Step 1: 写失败测试**

```python
# tests/integration/test_backfill_channel_urls.py —— 升级到上一版→插脏数据→升到 head→断言
# 复用 conftest 的 database_url fixture；alembic command API 参考 tests/integration/test_migrations.py 的用法
def test_backfill_appends_videos_only_for_bare_youtube(database_url):
    # 1) alembic downgrade 到 backfill 前一版（或 stamp）
    # 2) 原生 SQL 插 4 行：youtube 裸 channel / youtube 已带 /videos / douyin / url=NULL
    # 3) alembic upgrade head
    # 4) 断言：仅第 1 行被追加 /videos，其余原样；二次 upgrade head 幂等
```

（实现时先读 `test_migrations.py` 既有升级/降级 helper，保持同款写法；若其会话级 fixture 已升到 head，则在独立引擎/库上重放。）

- [ ] **Step 2: 跑测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://radar:radar@localhost:5544/radar" .venv/bin/pytest tests/integration/test_backfill_channel_urls.py -v`
Expected: FAIL（迁移不存在）

- [ ] **Step 3: 生成迁移并实现**

```bash
.venv/bin/alembic -c apps/api/alembic.ini revision -m "backfill youtube channel urls" --rev-id backfill_channel_urls
```

```python
def upgrade() -> None:
    # 注记③存量补扫：YouTube 裸频道地址补 /videos（注册侧已由 normalize_channel_url 兜新数据）
    op.execute("""
        UPDATE source_account
        SET url = url || '/videos', updated_at = now()
        WHERE platform = 'youtube'
          AND url IS NOT NULL
          AND url !~ '(/videos|/streams|/shorts|/featured|/playlists|/watch|list=)$|(/videos|/streams|/shorts|/featured|/playlists|/watch|list=)'
          AND url ~ '(youtube\.com/(channel/UC[\w-]{20,}|@[\w.\-]+|c/[\w.\-]+|user/[\w.\-]+))/?$'
    """)

def downgrade() -> None:
    # 一次性数据修复，不回滚（裸地址形态可由 /videos 再规范化得到，无信息损失）
    pass
```

（正则以能通过 Step 1 四行断言为准调平；`down_revision` 指向当前 head。）

- [ ] **Step 4: 跑测试确认通过 + 全迁移回归**

Run: `DATABASE_URL="postgresql+psycopg://radar:radar@localhost:5544/radar" .venv/bin/pytest tests/integration/test_backfill_channel_urls.py tests/integration/test_migrations.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/migrations/versions/ tests/integration/test_backfill_channel_urls.py
git commit -m "feat: 存量 YouTube 裸频道 URL 补扫迁移（注记③）"
```

---

## Task 5: 字幕获取与解析（RAD-031 前半）

**Files:**
- Modify: `apps/api/app/services/media/contracts.py`（`SubtitleResult` 充实）
- Modify: `apps/api/app/services/media/adapters/yt_dlp.py`（`fetch_subtitle` 实现）
- Create: `apps/api/app/services/media/subtitles.py`
- Test: `tests/unit/test_ytdlp_fetch_subtitle.py`、`tests/unit/test_subtitle_parse.py`

- [ ] **Step 1: 写失败测试（沿用 yt-dlp FAKE 二进制 harness 模式）**

```python
# tests/unit/test_ytdlp_fetch_subtitle.py —— FAKE yt-dlp 脚本：按参数写出一个 .zh-Hans.json3 文件
JSON3 = '{"events":[{"tStartMs":0,"dDurationMs":1500,"segs":[{"utf8":"今天"},{"utf8":"A股"}]},{"tStartMs":2000,"dDurationMs":1000,"segs":[{"utf8":"大涨"}]}]}'

def test_fetch_subtitle_returns_bytes(make, tmp_path):
    adapter = make(monkeypatch, writes=("sub.%(ext)s", ...))  # FAKE 脚本把 JSON3 写到 %(id)s.<lang>.json3
    result = adapter.fetch_subtitle(ItemRef(external_item_id="v1", canonical_url="https://www.youtube.com/watch?v=v1"), language="zh-Hans")
    assert result is not None and result.language == "zh-Hans" and result.fmt == "json3"  # ENG-5A：断言用全等，不用 or 短路糊弄
    assert result.content == JSON3.encode()

def test_fetch_subtitle_missing_returns_none(make):
    # FAKE 脚本不写任何文件
    assert adapter.fetch_subtitle(item, language="ja") is None

def test_fetch_subtitle_auto_flag_selects_switch(...):  # auto=True → 参数含 --write-auto-subs 且不含 --write-subs
```

```python
# tests/unit/test_subtitle_parse.py
from app.services.media.subtitles import is_usable, parse_subtitle
from app.services.media.contracts import SubtitleResult

def test_parse_json3(...):  # 事件→(0,1500,"今天A股")、(2000,3000,"大涨")；空 segs/换行事件被跳过
def test_parse_vtt(...):    # WEBVTT 块→毫秒段；NOTE/头被跳过
def test_not_usable_when_too_short(...):  # 总字符 < subtitle_min_chars → is_usable False
def test_unparsable_returns_empty(...):   # 坏载荷 → []（上层据此走 ASR，不抛）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_ytdlp_fetch_subtitle.py tests/unit/test_subtitle_parse.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

contracts.py：

```python
@dataclass(frozen=True)
class SubtitleResult:
    language: str
    content: bytes
    fmt: str = "vtt"    # json3 | vtt
    auto: bool = False
```

（注意：EPIC-02 既有测试若按位置构造不受影响。）

yt_dlp.py `fetch_subtitle`（替换占位）：

```python
    def fetch_subtitle(self, item: ItemRef, language: str | None = None, *, auto: bool = False) -> SubtitleResult | None:
        ensure_allowed_url(item.canonical_url, self._allowlist)
        with tempfile.TemporaryDirectory() as tmp:
            args = ["--skip-download", "--no-playlist", "--no-warnings",
                    "--write-auto-subs" if auto else "--write-subs",
                    "--sub-langs", language or "all",
                    "--sub-format", "json3/vtt",
                    "-o", str(Path(tmp) / "%(id)s.%(ext)s"), item.canonical_url]
            self._proc.run_json(args)
            files = sorted(Path(tmp).glob("*.json3")) or sorted(Path(tmp).glob("*.vtt"))
            if not files:
                return None
            f = files[0]
            return SubtitleResult(language=language or "", content=f.read_bytes(),
                                  fmt=f.suffix.lstrip("."), auto=auto)
```

（`YtDlpProcess.run_json` 增加可选 `timeout_sec` 参数覆盖默认，取字幕沿用 60s。）

subtitles.py：

```python
"""字幕解析（RAD-031）：json3/vtt → 段落；不可用即空列表，由编排层转 ASR。"""
import json
import re
from dataclasses import dataclass

from app.services.media.contracts import SubtitleResult

@dataclass(frozen=True)
class SubtitleSegment:
    start_ms: int
    end_ms: int
    text: str

_VTT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})")

def parse_subtitle(result: SubtitleResult) -> list[SubtitleSegment]:
    try:
        return _parse_json3(result.content) if result.fmt == "json3" else _parse_vtt(result.content)
    except (json.JSONDecodeError, KeyError, ValueError, IndexError):
        return []  # 坏载荷不致命：上层走 ASR

def is_usable(segments: list[SubtitleSegment], *, min_chars: int) -> bool:
    return sum(len(s.text) for s in segments) >= min_chars

def _parse_json3(raw: bytes) -> list[SubtitleSegment]:
    events = json.loads(raw).get("events") or []
    out = []
    for ev in events:
        segs = ev.get("segs")
        if not segs or ev.get("dDurationMs") in (None, 0, -1):
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        start = int(ev["tStartMs"]); end = start + int(ev["dDurationMs"])
        if end > start:
            out.append(SubtitleSegment(start, end, text))
    return out

def _parse_vtt(raw: bytes) -> list[SubtitleSegment]:
    out = []
    blocks = raw.decode("utf-8", errors="replace").split("\n\n")
    for block in blocks:
        m = _VTT_TIME.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = (g[0]*3600 + g[1]*60 + g[2])*1000 + g[3]
        end = (g[4]*3600 + g[5]*60 + g[6])*1000 + g[7]
        text = " ".join(block[m.end():].split()).strip()
        if text and end > start:
            out.append(SubtitleSegment(start, end, text))
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/unit/test_ytdlp_fetch_subtitle.py tests/unit/test_subtitle_parse.py tests/unit/test_media_contracts.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/media/ tests/unit/test_ytdlp_fetch_subtitle.py tests/unit/test_subtitle_parse.py
git commit -m "feat: yt-dlp 字幕抓取 + json3/vtt 解析（RAD-031 前半）"
```

---

## Task 6: 媒体下载（download_media）

**Files:**
- Modify: `apps/api/app/services/media/adapters/yt_dlp.py`
- Modify: `apps/api/app/core/settings.py`（`ytdlp_download_timeout_sec`）
- Test: `tests/unit/test_ytdlp_download.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_ytdlp_download.py —— FAKE yt-dlp：把伪音频写进 -o 模板展开后的路径
def test_download_returns_file(make):
    result = adapter.download_media(ItemRef(...), workdir=tmp_path)
    assert Path(result.local_path).exists() and Path(result.local_path).parent == tmp_path
    assert result.size_bytes == Path(result.local_path).stat().st_size

def test_download_no_output_raises(...):  # FAKE 什么都不写 → AdapterProcessError
def test_download_args_use_bestaudio(...):  # 断言 FAKE 收到 -f bestaudio/best
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_ytdlp_download.py -v`
Expected: FAIL（NotImplementedError）

- [ ] **Step 3: 实现**

```python
    def download_media(self, item: ItemRef, workdir: str | Path) -> DownloadResult:
        # workdir 归编排层所有（TemporaryDirectory 生命周期），adapter 只往里写
        ensure_allowed_url(item.canonical_url, self._allowlist)
        self._proc.run_json(
            ["-f", "bestaudio/best", "--no-playlist", "--no-warnings",
             "-o", str(Path(workdir) / "%(id)s.%(ext)s"), item.canonical_url],
            timeout_sec=self._download_timeout,
        )
        files = [f for f in Path(workdir).iterdir() if f.is_file() and f.suffix != ".part"]  # ENG-3A：跳过 yt-dlp 半成品残留
        if not files:
            raise AdapterProcessError("yt-dlp 未产出下载文件")
        f = files[0]
        return DownloadResult(local_path=str(f), size_bytes=f.stat().st_size)
```

（构造函数加 `download_timeout_sec: int = 600`，settings 注入；contracts.py `download_media` 签名同步加 `workdir`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/unit/test_ytdlp_download.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/media/ apps/api/app/core/settings.py tests/unit/test_ytdlp_download.py
git commit -m "feat: yt-dlp 音频下载到编排层 workdir（EPIC-03）"
```

---

## Task 7: FFmpeg 音频标准化（RAD-032）

**Files:**
- Create: `apps/api/app/services/media/audio.py`
- Modify: `apps/api/app/core/settings.py`（`ffmpeg_binary/ffmpeg_timeout_sec`）
- Test: `tests/unit/test_audio_normalize.py`

输出 mono/16kHz/wav；记录 input/output sha256 + duration；失败错误码 `MEDIA_FFMPEG_FAILED`（PRD 原文）。duration 用 stdlib `wave` 读头（ffmpeg 默认 wav=pcm_s16le），不引入 ffprobe。

- [ ] **Step 1: 写失败测试（FAKE ffmpeg：shell 脚本写一个合法 wav）**

```python
# tests/unit/test_audio_normalize.py —— 伪造 ffmpeg：把预生成的 16k mono wav 拷到 argv 中 -f wav 后的输出路径
import wave, pytest
from app.services.media.audio import MediaAudioError, normalize_audio

def _make_wav(path, seconds=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x01" * 16000 * seconds)

def test_normalize_reports_sha_and_duration(make_ffmpeg, tmp_path):
    src = tmp_path / "in.m4a"; src.write_bytes(b"fake-audio")
    out = normalize_audio(src, tmp_path, binary=make_ffmpeg, timeout_sec=30)
    assert out.duration_ms == 1000 and out.path.suffix == ".wav"
    assert len(out.input_sha256) == 64 and len(out.output_sha256) == 64

def test_nonzero_exit_raises_media_ffmpeg_failed(make_bad_ffmpeg, tmp_path):
    with pytest.raises(MediaAudioError) as e:
        normalize_audio(tmp_path / "in.m4a", tmp_path, binary=make_bad_ffmpeg)
    assert e.value.code == "MEDIA_FFMPEG_FAILED"

def test_binary_missing_raises(tmp_path):
    with pytest.raises(MediaAudioError):
        normalize_audio(tmp_path / "in.m4a", tmp_path, binary="/nonexistent/ffmpeg")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_audio_normalize.py -v`
Expected: FAIL ModuleNotFoundError

- [ ] **Step 3: 实现**

```python
"""FFmpeg 音频标准化（RAD-032）：任意媒体 → mono 16kHz wav。"""
import hashlib
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

_STDERR_TAIL = 500


class MediaAudioError(Exception):
    def __init__(self, message: str, *, code: str = "MEDIA_FFMPEG_FAILED") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class NormalizedAudio:
    path: Path
    input_sha256: str
    output_sha256: str
    duration_ms: int


def normalize_audio(input_path: Path, output_dir: Path, *, binary: str = "ffmpeg", timeout_sec: int = 600) -> NormalizedAudio:
    out = output_dir / f"{input_path.stem}.16k-mono.wav"
    argv = [binary, "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path),
            "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_sec, check=False)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        raise MediaAudioError(f"ffmpeg 超时（>{timeout_sec}s）") from exc
    except FileNotFoundError as exc:
        raise MediaAudioError(f"ffmpeg 二进制不存在: {binary!r}，检查 FFMPEG_BINARY") from exc
    if proc.returncode != 0 or not out.exists():
        raise MediaAudioError(f"ffmpeg 退出码 {proc.returncode}: {proc.stderr.strip()[-_STDERR_TAIL:]}")
    with wave.open(str(out), "rb") as w:
        duration_ms = int(w.getnframes() * 1000 / w.getframerate())
    return NormalizedAudio(path=out, input_sha256=_sha256(input_path), output_sha256=_sha256(out), duration_ms=duration_ms)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/pytest tests/unit/test_audio_normalize.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/media/audio.py apps/api/app/core/settings.py tests/unit/test_audio_normalize.py
git commit -m "feat: FFmpeg 音频标准化 mono/16k wav（RAD-032）"
```

---

## Task 8: TranscriptionProvider + FasterWhisper + WhisperX flag（RAD-033/035）

**Files:**
- Create: `apps/api/app/services/transcription/__init__.py`、`contracts.py`、`faster_whisper.py`、`whisperx_provider.py`
- Modify: `apps/api/pyproject.toml`（+`faster-whisper>=1.0`；optional-dependencies 增 `whisperx = [...]` 但默认不装）
- Modify: `apps/api/app/core/settings.py`（asr_* / enable_whisperx / enable_diarization）
- Test: `tests/unit/test_transcription_faster_whisper.py`、`tests/unit/test_transcription_whisperx.py`

模型惰性加载（首次 transcribe 才 build，worker 冷启动不炸）；单测注入假 model。WhisperX：flag 关（默认）完全不 import；flag 开但未安装 → 警告 + 回落 faster-whisper（V1 不依赖它才能成功）。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_transcription_faster_whisper.py —— 假 WhisperModel：monkeypatch faster_whisper.WhisperModel
def test_transcribe_maps_segments(fake_model_cls, tmp_path):
    fake_model_cls.return_value.transcribe.return_value = (iter([
        SimpleNamespace(start=0.0, end=1.5, text=" 今天 A股 ", avg_logprob=-0.1),
        SimpleNamespace(start=2.0, end=3.0, text="大涨", avg_logprob=-0.01),
    ]), SimpleNamespace(language="zh", duration=3.0))
    r = FasterWhisperProvider(model_name="small", device="cpu", compute_type="int8", beam_size=5).transcribe(str(wav), language="zh")
    assert r.language == "zh" and r.provider == "faster-whisper" and r.model == "small"
    assert r.segments[0].text == "今天 A股" and r.segments[0].start_ms == 0 and r.segments[0].end_ms == 1500
    assert 0 < r.segments[0].confidence <= 1

def test_transcribe_wraps_errors(fake_model_cls, tmp_path):  # transcribe 抛错 → TranscriptionError
```

```python
# tests/unit/test_transcription_whisperx.py —— sys.modules 注入假 whisperx / 移除模拟未安装
def test_flag_off_never_imports_whisperx():  # get_transcription_provider(enable_whisperx=False) → FasterWhisperProvider
def test_flag_on_but_not_installed_falls_back(monkeypatch):  # sys.modules 弹掉 whisperx → 警告 + FasterWhisperProvider
def test_flag_on_and_installed_returns_whisperx(monkeypatch):  # 假 whisperx 模块 → WhisperXProvider
def test_whisperx_provider_aligns_and_diarizes(fake_whisperx):  # 薄实现：load→transcribe→align(+可选 diarize) 映射到 TranscriptResult
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/pytest tests/unit/test_transcription_faster_whisper.py tests/unit/test_transcription_whisperx.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# contracts.py
"""ASR Provider 契约（RAD-033）。"""
from dataclasses import dataclass
from typing import Protocol


class TranscriptionError(Exception):
    """ASR 失败统一包装。"""


@dataclass(frozen=True)
class TranscriptSegmentResult:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None = None
    speaker: str | None = None


@dataclass(frozen=True)
class TranscriptResult:
    language: str | None
    provider: str
    model: str
    segments: tuple[TranscriptSegmentResult, ...]


class TranscriptionProvider(Protocol):
    def transcribe(self, path: str, language: str | None = None) -> TranscriptResult: ...
```

```python
# faster_whisper.py —— 模型惰性加载；置信度 = exp(avg_logprob) 截断 [0,1]
import math

from app.services.transcription.contracts import (
    TranscriptResult, TranscriptSegmentResult, TranscriptionError,
)

PROVIDER = "faster-whisper"


class FasterWhisperProvider:
    def __init__(self, *, model_name: str, device: str, compute_type: str, beam_size: int) -> None:
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._model = None  # 惰性：首次 transcribe 才加载（下载/载显存都贵）

    def _get_model(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError(f"faster-whisper 未安装: {exc}") from exc
            self._model = WhisperModel(self._model_name, device=self._device, compute_type=self._compute_type)
        return self._model

    def transcribe(self, path: str, language: str | None = None) -> TranscriptResult:
        try:
            segments_iter, info = self._get_model().transcribe(
                path, language=language, beam_size=self._beam_size, vad_filter=True)
            segments = tuple(
                TranscriptSegmentResult(
                    start_ms=int(s.start * 1000), end_ms=int(s.end * 1000), text=s.text.strip(),
                    confidence=round(min(max(math.exp(s.avg_logprob), 0.0), 1.0), 4),
                ) for s in segments_iter
            )
        except TranscriptionError:
            raise
        except Exception as exc:  # ctranslate2/下载/解码等一揽子
            raise TranscriptionError(f"faster-whisper 转录失败: {exc}") from exc
        return TranscriptResult(language=getattr(info, "language", None), provider=PROVIDER,
                                model=self._model_name, segments=segments)
```

```python
# whisperx_provider.py —— 薄实现：对齐词级时间戳；diarization 可选（RAD-035）
class WhisperXProvider:
    PROVIDER = "whisperx"
    def __init__(self, *, model_name, device, compute_type, enable_diarization=False): ...
    def transcribe(self, path, language=None) -> TranscriptResult:
        import whisperx  # 仅 flag 开启才 import
        audio = whisperx.load_audio(path)
        model = whisperx.load_model(self._model_name, self._device, compute_type=self._compute_type)
        result = model.transcribe(audio, language=language)
        align_model, meta = whisperx.load_align_model(result["language"], self._device)
        result = whisperx.align(result["segments"], align_model, meta, audio, self._device, return_char_alignments=False)
        segments = [...]  # segments→TranscriptSegmentResult（score→confidence）
        if self._enable_diarization:
            diarize = whisperx.DiarizationPipeline(); labels = diarize(audio)  # ENG-5A：只调一次，labels 复用
            result = whisperx.assign_word_speakers(labels, result)
            # speaker 写入 speaker_label
        return TranscriptResult(...)
```

```python
# __init__.py —— 工厂：flag 分支 + 缺库回落（V1 不依赖 whisperx 才能成功）
@lru_cache
def get_transcription_provider() -> TranscriptionProvider:
    s = get_settings()
    fw = FasterWhisperProvider(model_name=s.asr_model_name, device=s.asr_device,
                               compute_type=s.asr_compute_type, beam_size=s.asr_beam_size)
    if not s.enable_whisperx:
        return fw
    try:
        import whisperx  # noqa: F401
    except ImportError:
        logger.warning("whisperx_enabled_but_not_installed_fallback", to="faster-whisper")
        return fw
    return WhisperXProvider(model_name=s.asr_model_name, device=s.asr_device,
                            compute_type=s.asr_compute_type, enable_diarization=s.enable_diarization)
```

- [ ] **Step 4: 安装依赖并跑测试**

Run: `.venv/bin/pip install -e apps/api && .venv/bin/pytest tests/unit/test_transcription_faster_whisper.py tests/unit/test_transcription_whisperx.py -v`
Expected: PASS（测试不下载模型——假 model 注入）

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/transcription/ apps/api/pyproject.toml apps/api/app/core/settings.py tests/unit/test_transcription_*.py
git commit -m "feat: TranscriptionProvider 契约 + faster-whisper 实现 + WhisperX flag（RAD-033/035）"
```

---

## Task 9: media_asset / transcript 仓储（RAD-034 持久化层）

**Files:**
- Create: `apps/api/app/repositories/media_assets.py`、`transcripts.py`
- Test: `tests/integration/test_media_asset_repo.py`、`tests/integration/test_transcript_repo.py`

- [ ] **Step 1: 写失败测试**

```python
# 复用 db_session fixture 与 _make_account 建真实 FK 行（同 test_upsert_refresh_semantics.py 模式）
def test_record_and_list_media_assets(db_session):
    item = _make_item(db_session)
    repo = MediaAssetRepository(db_session)
    a1 = repo.record(item.id, asset_type="subtitle", storage_uri="s3://b/s", mime_type="application/json", size_bytes=10, sha256="x"*64)
    assert [x.id for x in repo.list_for_item(item.id)] == [a1.id]

def test_replace_for_item_swaps_atomically(db_session):
    item = _make_item(db_session)
    repo = TranscriptRepository(db_session)
    repo.replace_for_item(item.id, [Seg(0, 1000, "一"), Seg(1500, 2000, "二")])
    assert repo.count_for_item(item.id) == 2
    repo.replace_for_item(item.id, [Seg(0, 500, "新")])   # 重跑替换语义
    rows = repo.list_for_item(item.id)
    assert [r.text for r in rows] == ["新"] and rows[0].sequence_no == 0  # 连续从 0 编号

def test_replace_rolls_back_on_bad_segment(db_session):  # start>=end 的段 → 抛错且旧数据完好
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://radar:radar@localhost:5544/radar" .venv/bin/pytest tests/integration/test_media_asset_repo.py tests/integration/test_transcript_repo.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# media_assets.py
class MediaAssetRepository:
    def __init__(self, session: Session) -> None: self._session = session
    def record(self, source_item_id, *, asset_type, storage_uri, mime_type=None, size_bytes=None, sha256=None, duration_ms=None) -> MediaAsset:
        asset = MediaAsset(source_item_id=source_item_id, asset_type=asset_type, storage_uri=storage_uri,
                           mime_type=mime_type, size_bytes=size_bytes, sha256=sha256, duration_ms=duration_ms)
        self._session.add(asset); self._session.flush()
        return asset
    def list_for_item(self, source_item_id) -> list[MediaAsset]: ...

# transcripts.py
class TranscriptWrite(NamedTuple):  # 或 dataclass
    start_ms: int; end_ms: int; text: str
    confidence: float | None = None
    language: str | None = None
    speaker: str | None = None

class TranscriptRepository:
    def replace_for_item(self, source_item_id: int, segments: Sequence[TranscriptWrite]) -> int:
        # 同一 item 一次只保留一份 transcript（TRANSCRIBE 幂等键在 metadata_json.transcript）
        self._session.execute(delete(TranscriptSegment).where(TranscriptSegment.source_item_id == source_item_id))
        for seq, s in enumerate(segments):
            if s.start_ms >= s.end_ms: raise ValueError(f"段 {seq} 时间非法: {s.start_ms} >= {s.end_ms}")
            # ENG-4A：模型列名是 asr_confidence/speaker_label，TranscriptWrite 字段是 confidence/speaker——显式映射，禁止 **asdict(s)（会传错列名直接 TypeError）
            self._session.add(TranscriptSegment(source_item_id=source_item_id, sequence_no=seq,
                                                start_ms=s.start_ms, end_ms=s.end_ms, text=s.text,
                                                asr_confidence=s.confidence, language=s.language, speaker_label=s.speaker))
        return len(segments)
    def list_for_item / count_for_item: ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `DATABASE_URL="postgresql+psycopg://radar:radar@localhost:5544/radar" .venv/bin/pytest tests/integration/test_media_asset_repo.py tests/integration/test_transcript_repo.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/repositories/ tests/integration/test_media_asset_repo.py tests/integration/test_transcript_repo.py
git commit -m "feat: media_asset 与 transcript_segment 仓储（RAD-034）"
```

---

## Task 10: prepare_source_item 编排（RAD-031 核心 + 注记①幂等）

**Files:**
- Create: `apps/api/app/services/preparation.py`
- Test: `tests/integration/test_preparation_service.py`

流程（一个 Celery 任务内串行完成，中途状态也落库，崩溃可从 `failed` 重跑）：

```text
lock(item) → 状态门槛（discovered|failed，其余 skip）          # 注记①幂等
→ resolve(canonical_url) 补全可空字段 + subtitle 轨道 → resolved
→ 轨道偏好选语言 → fetch_subtitle → parse → usable?
   ├─ 是：字幕原文上传 storage → media_asset(subtitle) → media_ready → 段落即 transcript
   └─ 否：download_media(workdir) → ffmpeg 标准化 → wav 上传 → media_asset(audio) → media_ready
        → provider.transcribe(wav) → transcribing（进入前落库）
→ 校验段落（trim/去空/start<end/重叠阈值） → replace_for_item → 原始输出 JSON 上传
   → media_asset(transcript) → metadata_json.transcript={provider,model,language,media_sha256,segment_count}
→ language 回填 source_item.language → transcribed → commit
异常：rollback → 重锁 → status=failed + metadata_json.last_error={code,stage,message,at} → commit → 返回失败 dict（不重抛，无 autoretry 风暴）
```

- [ ] **Step 1: 写失败测试（全部外部依赖用 fake；真 DB）**

```python
# tests/integration/test_preparation_service.py
# fake adapter: resolve 返回富 ResolvedMedia（含/不含 subtitle 轨道两态）；fetch_subtitle 返回 json3 或 None
# fake storage: 字典实现 put_file/get_signed_url/exists/delete
# fake provider: 返回固定 TranscriptResult
def test_subtitle_path(db_session, fakes):        # 有 zh-Hans 轨道 → media_asset(subtitle)+segments，status=transcribed，未调用 provider
def test_asr_path_when_no_subtitle(db_session, fakes):  # 轨道空 → download+ffmpeg(fake)+ASR → media_asset(audio)+transcript，metadata_json.transcript 齐
def test_asr_path_when_subtitle_unusable(db_session, fakes):  # 字幕解析后 < min_chars → 走 ASR
def test_subtitle_fetch_error_falls_to_asr(db_session, fakes):  # CEO-2A：fetch_subtitle 抛 AdapterProcessError → 记 warning 后走 ASR，不 failed
def test_empty_transcript_rejected(db_session, fakes):  # CEO-4A：provider 返回 0 段 → failed + TRANSCRIPT_FAILED，绝不落 status=transcribed 空段落
def test_over_duration_guard(db_session, fakes):  # CEO-2C：duration_ms > prepare_max_media_duration_sec → failed + MEDIA_TOO_LONG，不进 ffmpeg/ASR
def test_duplicate_dispatch_skips(db_session, fakes):  # 第二次调用（status=transcribed）→ skipped，无副作用
def test_failure_records_error_code(db_session, fakes):  # fake provider 抛错 → status=failed，metadata_json.last_error.code=ASR_FAILED；再跑可从 failed 恢复成功
def test_enrich_backfills_only_null(db_session, fakes):  # 已有 title/published_at 不被 resolve 覆盖（与 upsert 语义一致）
def test_intermediate_status_persisted(db_session, fakes):  # fake provider 阶段性断言 status=media_ready（transcribing 前落库）
```

ffmpeg 在编排内调用方式：`normalize_audio(..., binary=settings.ffmpeg_binary)`——测试 monkeypatch `preparation.normalize_audio` 返回伪造 NormalizedAudio（单测已覆盖真函数）。

- [ ] **Step 2: 跑测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://radar:radar@localhost:5544/radar" .venv/bin/pytest tests/integration/test_preparation_service.py -v`
Expected: FAIL ModuleNotFoundError

- [ ] **Step 3: 实现 preparation.py**

```python
"""prepare_source_item 编排（RAD-031，注记①幂等）：resolve → 字幕优先 → ASR 兜底 → transcript。"""

_SEND = Callable[..., object]  # 预留 EPIC-04 下游派发（extract），本期不用

def prepare_source_item(item_id: int, session: Session, adapter: MediaSourceAdapter,
                        storage: Storage, provider: TranscriptionProvider) -> dict:
    s = get_settings()
    item = _lock_item(session, item_id)
    if item is None:
        return {"item_id": item_id, "skipped": "missing"}
    if item.status not in ("discovered", "failed"):
        return {"item_id": item_id, "skipped": item.status}   # 注记①：已推进/处理中 → 幂等跳过
    stage = "resolve"
    try:
        media = _enrich(session, item, adapter)               # → status=resolved
        with tempfile.TemporaryDirectory() as workdir:
            segs, origin = _subtitle_or_asr(session, item, adapter, storage, provider,
                                            media, Path(workdir), s)
            stage = "persist"
            _persist_transcript(session, item, storage, segs, origin, s)   # → transcribed
        return {"item_id": item_id, "status": "transcribed", "origin": origin,
                "segments": len(segs)}
    except Exception as exc:
        return _record_failure(session, item_id, stage, exc)
```

要点（完整实现按测试补齐）：
- `_lock_item`：`session.execute(select(SourceItem).where(...).with_for_update())`。**ENG-1A 阶段间重锁**：commit 会释放行锁——中途状态落库后，下一阶段开始时必须重取行（再次 `SELECT … FOR UPDATE` + 状态门槛复核）再推进；既不长期占锁/占连接（配合 CEO-1A 低并发），又保证跨阶段无双进（状态门槛拦住并发派发）。
- `_enrich`：`media = adapter.resolve(item.canonical_url)`；仅当现值为 None 才回填 title/thumbnail_url/published_at/duration_ms；metadata 合并轨道信息；`ensure_transition(item.status, "resolved")` 后置值、commit（中途状态落库）。
- `_subtitle_or_asr`：按 `subtitle_lang_preference` 依次前缀匹配 `media.subtitles`（manual 优先于 auto）；命中→`fetch_subtitle(auto=track.is_auto)`→`parse_subtitle`→`is_usable` 不达标、`None`、或 **fetch/parse 抛 AdapterError（CEO-2A：字幕是 best-effort 优化，失败记 warning 降级，不 failed）** → ASR 路。ASR 路入口先做时长闸（CEO-2C）：`media.duration_ms > prepare_max_media_duration_sec*1000` → 抛 `MediaTooLongError(code="MEDIA_TOO_LONG")`；然后 `download_media(item, workdir)`→`normalize_audio`→`put_file(f"audio/{item_id}.wav")`→`media_asset(audio, sha256=output, duration_ms)`→`ensure_transition→media_ready` commit→`ensure_transition→transcribing` commit→`provider.transcribe(wav)`。
- 字幕路：`put_file(f"subtitles/{item_id}.{lang}.{fmt}")`→`media_asset(subtitle)`→media_ready。
- `_persist_transcript`：段落清洗（strip/去空/`start<end` 违例抛 `TranscriptValidationError`/相邻重叠超 `transcript_overlap_tolerance_ms` 抛错/重编号/**清洗后 0 段抛 `TranscriptValidationError`（CEO-4A：空 transcript 是失败不是成功）**）→`TranscriptRepository.replace_for_item`→原始输出写临时 JSON `put_file(f"transcripts/{item_id}.{provider}.{model}.json")`→`media_asset(transcript)`→`metadata_json = {**old, "transcript": {...}}`（不可变重建）→`language` 回填→`ensure_transition→transcribed`→commit。
- `_record_failure`：rollback→重锁→`ensure_transition(status→failed)`（`transcribed` 等终态半路失败不该覆盖，若迁移非法则仅记日志）→`metadata_json.last_error={"code": getattr(exc,"code",_STAGE_CODES[stage]), "stage": stage, "message": str(exc)[:500], "at": now_iso}`→commit→`logger.error("prepare_failed", ...)`→返回 dict（**不重抛**：无 autoretry，重跑靠补扫/手工 retry）。
- `_STAGE_CODES = {"resolve": "RESOLVE_FAILED", "download": "DOWNLOAD_FAILED", "normalize": "MEDIA_FFMPEG_FAILED", "asr": "ASR_FAILED", "persist": "TRANSCRIPT_FAILED"}`（`MediaAudioError.code` 自带）。

- [ ] **Step 4: 跑测试确认通过**

Run: `DATABASE_URL="postgresql+psycopg://radar:radar@localhost:5544/radar" .venv/bin/pytest tests/integration/test_preparation_service.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/services/preparation.py tests/integration/test_preparation_service.py
git commit -m "feat: prepare_source_item 编排——字幕优先/ASR 兜底/幂等行锁（RAD-031，注记①）"
```

---

## Task 11: Worker 任务与存量补扫（注记②）

**Files:**
- Modify: `apps/api/app/worker/tasks.py`（+`prepare_source_item`、+`dispatch_pending_prepares`）
- Modify: `apps/api/app/worker/celery_app.py`（beat 增补扫条目）
- Modify: `apps/api/app/repositories/source_items.py`（+`list_by_status`）
- Modify: `apps/api/app/core/settings.py`（`prepare_sweep_*`）
- Test: `tests/integration/test_prepare_tasks.py`、`tests/unit/test_worker_task_registry.py`（追加两个任务名）

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_worker_task_registry.py 追加
def test_prepare_tasks_registered():
    celery_app.loader.import_default_modules()
    names = set(celery_app.tasks)
    assert "prepare_source_item" in names and "dispatch_pending_prepares" in names
```

```python
# tests/integration/test_prepare_tasks.py
def test_prepare_task_runs_pipeline(db_session, fakes):  # 建 discovered item → task.run(item_id)（绕 broker，同 EPIC-02 模式）→ status=transcribed
def test_sweep_dispatches_discovered(db_session, fake_send):  # 建 2 条 discovered + 1 条 transcribed → dispatch_pending_prepares() → send 恰好 2 次且 name 正确
def test_sweep_respects_batch_size(db_session, fake_send):  # 批上限截断
```

- [ ] **Step 2: 跑测试确认失败**

Run: `DATABASE_URL="postgresql+psycopg://radar:radar@localhost:5544/radar" .venv/bin/pytest tests/integration/test_prepare_tasks.py tests/unit/test_worker_task_registry.py -v`
Expected: FAIL（任务不存在）

- [ ] **Step 3: 实现**

worker/tasks.py 追加：

```python
@celery_app.task(name="prepare_source_item")  # 注记①：幂等在编排层（行锁+状态门槛），重复投递安全
def prepare_source_item(item_id: int) -> dict:
    from app.services.preparation import prepare_source_item as run_prepare
    from app.services.storage import get_storage
    from app.services.transcription import get_transcription_provider
    session = get_session_factory()()
    try:
        return run_prepare(item_id, session, build_adapter(), get_storage(), get_transcription_provider())
    finally:
        session.close()


@celery_app.task(name="dispatch_pending_prepares")  # 注记②：周期补扫 discovered（G1 兜底）
def dispatch_pending_prepares() -> int:
    from app.repositories.source_items import SourceItemRepository
    s = get_settings()
    session = get_session_factory()()
    try:
        pending = SourceItemRepository(session).list_by_status("discovered", limit=s.prepare_sweep_batch_size)
        for item in pending:
            celery_app.send_task("prepare_source_item", args=[item.id])
        logger.info("dispatch_pending_prepares", dispatched=len(pending))
        return len(pending)
    finally:
        session.close()
```

`SourceItemRepository.list_by_status(status, *, limit)`：`select(SourceItem).where(status==...).order_by(SourceItem.id).limit(limit)`。

celery_app.py beat_schedule 追加 `"dispatch-pending-prepares": {"task": "dispatch_pending_prepares", "schedule": s.prepare_sweep_interval_sec}`（沿用既有条目写法）。

- [ ] **Step 4: 跑测试确认通过**

Run: `DATABASE_URL="postgresql+psycopg://radar:radar@localhost:5544/radar" .venv/bin/pytest tests/integration/test_prepare_tasks.py tests/unit/test_worker_task_registry.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add apps/api/app/worker/ apps/api/app/repositories/source_items.py apps/api/app/core/settings.py tests/
git commit -m "feat: prepare_source_item 任务 + discovered 周期补扫（注记②）"
```

---

## Task 12: 部署面与文档收口

**Files:**
- Modify: `infra/docker/Dockerfile.api`（apt 装 `ffmpeg`；`pip install .` 自然带 faster-whisper/boto3）
- Modify: `.env.example`（新增 EPIC-03 键 + `HF_ENDPOINT=https://hf-mirror.com` 注释说明）
- Modify: `README.md`（已完成/TODO/排障：ffmpeg 缺失、ASR 模型下载慢、抖音无账号发现说明）
- Modify: `02_finance_opinion_radar_execution_plan.md`（勾选 RAD-030~035；注记①-④落地情况回填）

- [ ] **Step 1: Dockerfile 加 ffmpeg**

runtime 阶段 `apt-get install -y --no-install-recommends ffmpeg`（与既有 yt-dlp 安装行同段），重建镜像验证：`docker build -t radar-api-dev infra/docker/ -f infra/docker/Dockerfile.api && docker run --rm radar-api-dev ffmpeg -version`。

- [ ] **Step 2: .env.example 增补**

```bash
# --- EPIC-03 媒体与 ASR ---
# FFMPEG_BINARY=ffmpeg
# FFMPEG_TIMEOUT_SEC=600
# YTDLP_DOWNLOAD_TIMEOUT_SEC=600
# SUBTITLE_LANG_PREFERENCE=zh-Hans,zh,en
# ASR_MODEL_NAME=small        # 首次运行自动从 HuggingFace 下载模型
# ASR_DEVICE=cpu / ASR_COMPUTE_TYPE=int8 / ASR_BEAM_SIZE=5
# ENABLE_WHISPERX=false       # V1 默认关；开启需 pip install '.[whisperx]'
# PREPARE_SWEEP_INTERVAL_SEC=600
# 模型下载慢/失败时（国内网络）：
# HF_ENDPOINT=https://hf-mirror.com
```

- [ ] **Step 3: README 更新**

已完成节追加 RAD-030~035 条目；"手工解析一个视频"节后追加"自动转录（EPIC-03）"示例（`POST /source-items` 后 worker 自动 prepare；或 `celery_app.send_task` 提示；示例末尾附验收查询：`docker compose exec postgres psql -U radar -d radar -c "SELECT sequence_no, start_ms, end_ms, left(text,30) FROM transcript_segment WHERE source_item_id=<id> ORDER BY sequence_no LIMIT 5;"`，让"变出 transcript"可被直接看见——DX-2A）；排障表追加：ffmpeg 不存在、ASR 模型下载失败（HF_ENDPOINT）、抖音账号不可定时发现（yt-dlp 能力边界，贴单视频链接）、**首次 ASR 启动慢属正常**（首次 transcribe 才从 HF 拉 ~460MB 模型，非卡死——DX-1A）；排障表后附**错误码速查表**（DX-3A，满足 PRD DoD"日志与错误码"）：`RESOLVE_FAILED`（resolve 阶段失败→重试或查链接）/`DOWNLOAD_FAILED`（下载失败→网络或风控）/`MEDIA_FFMPEG_FAILED`（标准化失败→查 ffmpeg 二进制与源文件）/`ASR_FAILED`（转录失败→查模型/资源）/`TRANSCRIPT_FAILED`（段落校验失败→看 last_error.message）/`MEDIA_TOO_LONG`（超 prepare_max_media_duration_sec→拆条或调大阈值），均读自 `source_item.metadata_json.last_error`。另加两条运维注记（CEO-1A/9A）：**worker 并发指引**——ASR 是 CPU 密集，`make worker-beat` 建议低并发（如 `--concurrency=2`），高并发只会互相抢 CPU 且长期占 DB 连接；**Docker 内 HF 模型缓存**——容器化 worker 需挂载模型缓存卷（如 `~/.cache/huggingface`），否则每次重启重新下载 ~460MB。TODO 节更新为 EPIC-04+。

- [ ] **Step 4: 执行计划勾选与回填**

RAD-030~035 逐条勾选；EPIC-03 顶部注记①-④各补一行"落地于 <commit/task>"。

- [ ] **Step 5: 全量回归**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check apps/api tests && .venv/bin/mypy apps/api`
Expected: 全绿；覆盖率 ≥80%。

- [ ] **Step 6: 提交**

```bash
git add infra/docker/Dockerfile.api .env.example README.md 02_finance_opinion_radar_execution_plan.md
git commit -m "chore: EPIC-03 部署面与文档收口（ffmpeg/环境变量/README/执行计划）"
```

---

## 验收（PRD §16.4：一个 URL 能变 transcript）

1. 全部单测/集成/lint/mypy 绿，CI 6 jobs 绿。
2. 真栈演示（/qa 阶段执行）：贴一条真实抖音视频链接 → `resolve-url` → `POST /source-items` → worker `prepare_source_item` → `transcript_segment` 出现带时间戳中文段落，`source_item.status='transcribed'`，MinIO 里可见 subtitle/audio/transcript 对象。
3. 重复派发 `prepare_source_item` 同一 item → `skipped`，无重复段落（注记①）。
4. 手工把某条 item 状态改回 `discovered` → ≤10 分钟补扫任务接管（注记②）。

## 风险与边界（实现时注意）

- **抖音下载签名/风控**：yt-dlp 抖音提取器可能需要 cookies；/qa 真栈验证时如遇 403，记录并评估 `--cookies-from-browser`/config_json 通道，不在本计划擅自加。
- **faster-whisper 模型下载**（small ~460MB）：CI 不下载（全部假 model）；本地/真栈首次下载走 HF，慢则 HF_ENDPOINT。
- **60s resolve 超时对长下载不适用**：下载/取字幕独立超时（600s），复用 `YtDlpProcess` 注入。
- **VAD**：`vad_filter=True` 默认开（faster-whisper 内置，减静音段）；如真栈异常再降级。

## 实施记录

（执行时逐任务追加：commit、偏离、发现。）

<!-- autoplan-accepted:ceo -->
- CEO-2A: fetch/parse 字幕抛 AdapterError → warning 降级 ASR，不 failed（Task 10 _subtitle_or_asr + test_subtitle_fetch_error_falls_to_asr）
- CEO-2C: prepare_max_media_duration_sec=14400 时长闸 → MediaTooLongError(code=MEDIA_TOO_LONG)（settings + Task 10 + test_over_duration_guard）
- CEO-4A: 清洗后 0 段抛 TranscriptValidationError → TRANSCRIPT_FAILED（Task 10 _persist_transcript + test_empty_transcript_rejected）
- CEO-1A: README worker 低并发指引（--concurrency=2，ASR CPU 密集 + 长期占 DB 连接）（Task 12）
- CEO-9A: README Docker HF 模型缓存卷注记（~/.cache/huggingface，~460MB）（Task 12）
  外审状态：unavailable（codex CLI 0.137.0 过旧不支持 gpt-6-astra；gpt-5.1-codex exit 124）
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:dx -->
- DX-1A: README 排障表补"首次 ASR 启动慢属正常"注记（首次 transcribe 才从 HF 拉 ~460MB 模型）（Task 12 Step 3）
- DX-2A: README"自动转录"示例末尾附 psql 验收查询（transcript_segment 可直接看见）（Task 12 Step 3）
- DX-3A: README 排障表后附错误码速查表（RESOLVE_FAILED/DOWNLOAD_FAILED/MEDIA_FFMPEG_FAILED/ASR_FAILED/TRANSCRIPT_FAILED/MEDIA_TOO_LONG + 读自 metadata_json.last_error）（Task 12 Step 3）
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- ENG-1A: Task 10 编排加"阶段间重锁"注记——commit 释放行锁后每阶段重取 FOR UPDATE + 状态门槛复核，不长期占锁且跨阶段无双进
- ENG-2A: Task 3 补 2 条接线测试——API NotSingleItemError→400 用例（test_api_source_items.py）+ 注册侧 normalize 生效断言（upsert 集成测试）
- ENG-3A: Task 6 download_media 文件扫描跳过 .part 残留（yt-dlp 半成品）
- ENG-4A: Task 9 TranscriptWrite→TranscriptSegment 显式字段映射（confidence→asr_confidence、speaker→speaker_label），禁 **asdict(s)
- ENG-5A: Task 5 断言改全等（拆两行）；Task 8 修 PROVIDER 行首空格；whisperx sketch 复用 diarize labels 不重复调用
<!-- /autoplan-accepted:eng -->
## Review record
<!-- autoplan-baseline-edits:ceo {"sourceSha256":"fdfc59a879685810a3fcbc7862f7a1ed77e73f4964b54664eefcf5254b8ff421","replacements":[{"oldText":"transcript_overlap_tolerance_ms: int = 2000    # RAD-034 重叠阈值\n```\n","newText":"transcript_overlap_tolerance_ms: int = 2000    # RAD-034 重叠阈值\nprepare_max_media_duration_sec: int = 14400    # CEO-2C：直播回放可达数小时，超限快速失败防 CPU 长期占用\n```\n"},{"oldText":"def test_asr_path_when_subtitle_unusable(db_session, fakes):  # 字幕解析后 < min_chars → 走 ASR\ndef test_duplicate_dispatch_skips(db_session, fakes):  # 第二次调用（status=transcribed）→ skipped，无副作用\n","newText":"def test_asr_path_when_subtitle_unusable(db_session, fakes):  # 字幕解析后 < min_chars → 走 ASR\ndef test_subtitle_fetch_error_falls_to_asr(db_session, fakes):  # CEO-2A：fetch_subtitle 抛 AdapterProcessError → 记 warning 后走 ASR，不 failed\ndef test_empty_transcript_rejected(db_session, fakes):  # CEO-4A：provider 返回 0 段 → failed + TRANSCRIPT_FAILED，绝不落 status=transcribed 空段落\ndef test_over_duration_guard(db_session, fakes):  # CEO-2C：duration_ms > prepare_max_media_duration_sec → failed + MEDIA_TOO_LONG，不进 ffmpeg/ASR\ndef test_duplicate_dispatch_skips(db_session, fakes):  # 第二次调用（status=transcribed）→ skipped，无副作用\n"},{"oldText":"- `_subtitle_or_asr`：按 `subtitle_lang_preference` 依次前缀匹配 `media.subtitles`（manual 优先于 auto）；命中→`fetch_subtitle(auto=track.is_auto)`→`parse_subtitle`→`is_usable` 不达标或 `None` → ASR 路。ASR 路：`download_media(item, workdir)`→`normalize_audio`→`put_file(f\"audio/{item_id}.wav\")`→`media_asset(audio, sha256=output, duration_ms)`→`ensure_transition→media_ready` commit→`ensure_transition→transcribing` commit→`provider.transcribe(wav)`。\n","newText":"- `_subtitle_or_asr`：按 `subtitle_lang_preference` 依次前缀匹配 `media.subtitles`（manual 优先于 auto）；命中→`fetch_subtitle(auto=track.is_auto)`→`parse_subtitle`→`is_usable` 不达标、`None`、或 **fetch/parse 抛 AdapterError（CEO-2A：字幕是 best-effort 优化，失败记 warning 降级，不 failed）** → ASR 路。ASR 路入口先做时长闸（CEO-2C）：`media.duration_ms > prepare_max_media_duration_sec*1000` → 抛 `MediaTooLongError(code=\"MEDIA_TOO_LONG\")`；然后 `download_media(item, workdir)`→`normalize_audio`→`put_file(f\"audio/{item_id}.wav\")`→`media_asset(audio, sha256=output, duration_ms)`→`ensure_transition→media_ready` commit→`ensure_transition→transcribing` commit→`provider.transcribe(wav)`。\n"},{"oldText":"- `_persist_transcript`：段落清洗（strip/去空/`start<end` 违例抛 `TranscriptValidationError`/相邻重叠超 `transcript_overlap_tolerance_ms` 抛错/重编号）→`TranscriptRepository.replace_for_item`→原始输出写临时 JSON `put_file(f\"transcripts/{item_id}.{provider}.{model}.json\")`→`media_asset(transcript)`→`metadata_json = {**old, \"transcript\": {...}}`（不可变重建）→`language` 回填→`ensure_transition→transcribed`→commit。\n","newText":"- `_persist_transcript`：段落清洗（strip/去空/`start<end` 违例抛 `TranscriptValidationError`/相邻重叠超 `transcript_overlap_tolerance_ms` 抛错/重编号/**清洗后 0 段抛 `TranscriptValidationError`（CEO-4A：空 transcript 是失败不是成功）**）→`TranscriptRepository.replace_for_item`→原始输出写临时 JSON `put_file(f\"transcripts/{item_id}.{provider}.{model}.json\")`→`media_asset(transcript)`→`metadata_json = {**old, \"transcript\": {...}}`（不可变重建）→`language` 回填→`ensure_transition→transcribed`→commit。\n"},{"oldText":"已完成节追加 RAD-030~035 条目；\"手工解析一个视频\"节后追加\"自动转录（EPIC-03）\"示例（`POST /source-items` 后 worker 自动 prepare；或 `celery_app.send_task` 提示）；排障表追加：ffmpeg 不存在、ASR 模型下载失败（HF_ENDPOINT）、抖音账号不可定时发现（yt-dlp 能力边界，贴单视频链接）。TODO 节更新为 EPIC-04+。\n","newText":"已完成节追加 RAD-030~035 条目；\"手工解析一个视频\"节后追加\"自动转录（EPIC-03）\"示例（`POST /source-items` 后 worker 自动 prepare；或 `celery_app.send_task` 提示）；排障表追加：ffmpeg 不存在、ASR 模型下载失败（HF_ENDPOINT）、抖音账号不可定时发现（yt-dlp 能力边界，贴单视频链接）。另加两条运维注记（CEO-1A/9A）：**worker 并发指引**——ASR 是 CPU 密集，`make worker-beat` 建议低并发（如 `--concurrency=2`），高并发只会互相抢 CPU 且长期占 DB 连接；**Docker 内 HF 模型缓存**——容器化 worker 需挂载模型缓存卷（如 `~/.cache/huggingface`），否则每次重启重新下载 ~460MB。TODO 节更新为 EPIC-04+。\n"}]} -->

### Phase 1 — CEO Review（/autoplan，2026-09-17）

**Snapshots:** plan `sha256 fdfc59a87968…`（63,378 bytes / 1,403 行，snapshot `autoplan-ceo-G2RWWy/ceo-implementation.md`）；methodology `2be93f5d…`（2266 行）。审查对象 = 该快照；本记录含 5 处修正后的正文，`amend` 后快照与正文一致。
**Voices:** Codex = **unavailable**（`codex exec` 默认模型 gpt-6-astra 报"requires a newer version of Codex"，CLI 0.137.0 过旧；改试 gpt-5.1-codex → exit 124 超时 + chatgpt.com TLS/网络错误。修复提示：`npm i -g @openai/codex` 升级 CLI 或设 `GSTACK_CODEX_MODEL` 为账号可用模型）。Claude = **in-host native pass**（用户既定约定：不派 subagent，主会话内执行）。Consensus = **N/A**（外审缺席，单通道）。

#### 0A. Premise challenge（前提证伪）

| # | 前提 | 判定 | 依据 |
|---|---|---|---|
| P1 | "抖音账号可定时发现新视频" | **部分证伪，已改写** | yt-dlp 2026.03.17 实测 `douyin.com/user/…` → `Unsupported URL`（无用户页列表能力）；计划已改写为"抖音 = 手工贴单视频链接为主"，YouTube 保留账号发现 |
| P2 | "直播回放按普通 VOD 走同一条管线" | 成立 | 直播回放是平台侧 VOD 对象，yt-dlp 单视频提取器可解析；实时值守是 V1.5（PRD L51/L210/§14），不在本期 |
| P3 | "字幕优先能省掉大部分 ASR 成本" | 成立（抖音侧存疑） | YouTube 字幕轨道丰富成立；抖音短视频几乎无字幕轨道 → ASR 为主。管线两条路都保留，与 P3 无冲突 |
| P4 | "faster-whisper small / cpu / int8 在本机可行" | 成立（待 /qa 证实） | 用户机器 ffmpeg 8 / Docker 可用；460MB 模型下载走 HF（HF_ENDPOINT 注记兜底）。真栈性能 /qa 验证 |
| P5 | "EPIC-02 已按名投递 prepare_source_item(item_id)，本期只需实现任务体" | 成立 | 代码核实 `discovery.py` `send_task("prepare_source_item")`；注记①② 幂等 + 补扫即为其兜底 |
| P6 | "PRD §11 状态机是唯一状态权威" | 成立 | `models/source.py` status 默认 `discovered`，无代码级 guard → Task 1 补 guard 是净新增，无冲突 |

**Gate items（clearly-wrong premises）: 0。** P1 在 D1 对话阶段已被用户答案纠正并改写入计划正文，进入本轮时前提已干净。

#### 0B. Leverage map（已有代码杠杆）

| 已有资产 | 本计划复用方式 |
|---|---|
| `contracts.py`（ResolvedMedia/SubtitleResult/DownloadResult/Adapter Protocol + 错误族） | 充实 SubtitleResult（带默认值新字段）、+NotSingleItemError；旧构造不破坏 |
| `yt_dlp.py` YtDlpProcess 子进程 harness + FAKE 二进制测试模式 | fetch_subtitle/download_media 直接在同一 harness 上扩展；`run_json` 加可选 timeout_sec |
| `url_guard.py` 白名单 | fetch_subtitle/download_media 入口复用 `ensure_allowed_url`；子域后缀匹配已覆盖 v.douyin.com/live.douyin.com |
| `worker/tasks.py` build_adapter + send-by-name + beat 模式（EPIC-02 已修 ISSUE-001 include 注册） | prepare/dispatch 两个任务沿用同款注册与派发写法 |
| `settings.py` s3_* 配置块（已存在）与 CSV validator 模式 | storage 工厂直接读；subtitle_lang_preference 复用同款 validator |
| `conftest.py` _test_db_url（radar_test 派生）+ 集成测试 fixture 模式 | Task 4/9/10/11 集成测试直接复用 |
| Alembic 迁移测试模式（test_migrations.py） | Task 4 backfill 迁移测试同款升级/降级写法 |

#### 0C. Dream state（用户视角的"完成"）

```text
CURRENT（EPIC-02 后）                THIS PLAN（EPIC-03 后）              12-MONTH（V1 全量）
─────────────────────────           ─────────────────────────           ─────────────────────────
贴 YouTube 链接 → resolve           贴抖音/YouTube 单视频链接            全自动雷达：
能看标题/时长/字幕可用性            → 无人工干预得到带时间戳              账号定时发现 → transcript
抖音链接仅此而已                    中文 transcript（transcript_segment   → 观点抽取 → 证据链 → 人工复核
discovered 条目滞留无后续            带时间戳/置信度/说话人槽）            → 人物时间线 + 主题共识
（G1：send 失败即孤儿）             幂等：重复投递安全（行锁+门槛）       → Dashboard/搜索/Review Queue
                                    自愈：补扫任务 ≤10min 接管孤儿        直播实时值守（V1.5）
                                    崩溃可重跑：failed → resolved         质量门禁（golden dataset）
```

**Dream-state delta（本计划交付的部分）:** "一个 URL 能变 transcript"（执行计划 §15 第 4 条门槛）+ 幂等/自愈骨架。不交付：transcript 的任何读取 API（EPIC-07）、观点抽取（EPIC-04+）。

#### 0C-bis. Alternatives（被否决的路线）

| 方案 | 否决理由 |
|---|---|
| B. 托管 ASR API（OpenAI Whisper API / 阿里云听悟） | PRD §0.2 明确 in-house faster-whisper；长视频按时长计费成本失控；隐私/出网审查（抖音内容）不可控 |
| C. fork VideoLingo / Buzz 等一体化工具 | 违反"平台采集逻辑不散落"开发原则；把转录耦合进第三方 UI 工具，丢掉 pipeline 状态机与 media_asset 审计链 |
| D. 直播实时录制先做（用户"直播为主"表述的激进解读） | PRD 明确 V1 只做 VOD、V1.5 才 Live（"先 VOD、后 Live"总原则）；回放即 VOD 已覆盖直播内容的大头 |

#### 0F. Mode: SELECTIVE EXPANSION

理由：绿地增量 EPIC（RAD-030~035 编号既定、上下游契约已被 EPIC-01/02 钉死），扩展面集中在媒体/ASR 服务层。CEO 权限 = 选择性吸收边界发现（2A/2C/4A 三个补丁 + 1A/9A 运维注记），不重排任务结构、不砍 PRD 明确条目。

#### 0D. User challenges（需用户裁决的口味题）: **0**

全部决策均由 PRD 原文、用户 D1 答案（抖音单视频链接为主）或上游 /qa 记录（ISSUE-003 → 注记③）唯一锚定，无真歧义。

#### 0E. Sections 1–10 findings

| Section | 发现 | 裁决 |
|---|---|---|
| 1. 交付物完整性 | RAD-030~035 全覆盖 + 注记①-④ 全部落地到任务；WhisperX 有薄实现 + flag + 回落，满足"V1 不依赖" | ✅ 通过 |
| 2. 失败路径 | **2A**：fetch_subtitle/parse 抛 AdapterError 时原计划直接 failed——但字幕是优化项不是依赖项，失败应降级 ASR；**2C**：直播回放可达数小时，无时长闸会让一条 6h 视频吃满 CPU+DB 连接数小时 | ✅ 已修正正文：2A 降级记 warning 走 ASR；2C `prepare_max_media_duration_sec=14400` 时长闸 → `MEDIA_TOO_LONG`；各配验证测试 |
| 3. 数据契约 | SubtitleResult 增字段带默认值（向后兼容）；metadata_json.transcript 承载 TRANSCRIBE 幂等键为 EPIC-04 留桩；media_asset asset_type 扩展值不做迁移（docstring 注明） | ✅ 通过 |
| 4. 验证策略 | 单测全部 fake 注入（不下载模型/不碰网络），集成测试真 PG + 不可达即 skip（MinIO）；**4A**：原计划清洗后 0 段会落 status=transcribed 空段落——空 transcript 是失败不是成功 | ✅ 已修正：4A 清洗后 0 段抛 TranscriptValidationError → TRANSCRIPT_FAILED |
| 5. 幂等与并发 | 行锁 + 状态门槛（仅 discovered/failed 可进）；长任务持锁期间 DB 连接占用 → 1A 运维注记（低并发）而非代码改动；补扫批 200 与 beat 600s 匹配 | ✅ 通过（1A 落 README） |
| 6. 边界与安全 | url_guard 复用于新入口；FFmpeg/yt-dlp 子进程均 no-shell、超时独立；SSRF 面无新增 | ✅ 通过 |
| 7. 复用与一致性 | yt-dlp FAKE harness、settings validator、集成测试 fixture 全部复用既有模式；无平行抽象 | ✅ 通过 |
| 8. 范围蔓延 | 抖音 cookies、transcript GET API、status 索引均被拒入（见 NOT-in-scope） | ✅ 通过 |
| 9. 运维可行性 | **9A**：容器化 worker 每次重启重下 460MB 模型（无缓存卷）；ffmpeg 缺失在容器内才暴露 | ✅ 已修正：9A README 加 HF 缓存卷注记；Dockerfile 加 ffmpeg + 验证命令 |
| 10. 文档与交接 | README 排障表、.env.example、执行计划勾选回填均有任务位 | ✅ 通过 |

#### NOT in scope（明确不做，防蔓延）

- transcript 读取 API（GET）→ EPIC-07 契约层
- 抖音 cookies / `--cookies-from-browser` 通道 → 等 /qa 真栈 403 证据（风险节已挂）
- `source_item.status` 索引 → YAGNI（V1 量级 <1e4 行，seq scan 足够）
- 各阶段耗时埋点进 metadata_json → EPIC-05 pipeline_log/job_run
- 实时直播录制（DouyinLiveRecorder）→ V1.5
- WhisperX 强制可用（默认 flag 关 + 缺库回落即满足 RAD-035）

#### What-already-exists（不重做的）

EPIC-01 全部表结构（media_asset/transcript_segment 列已齐，无迁移加列）；EPIC-02 的 resolve-url API、账号发现、beat 骨架、url_guard、yt-dlp harness；Docker/CI 六 job 基线。

#### Error & Rescue Registry（错误与抢救）

| 错误场景 | 抢救路径 |
|---|---|
| resolve/download/ffmpeg/ASR 各阶段抛错 | `_record_failure`：rollback → 重锁 → failed + `metadata_json.last_error={code,stage,message,at}` → 返回失败 dict 不重抛；重跑入口 = 补扫或手工 |
| 字幕抓取抛 AdapterError | 2A：warning 降级 ASR，不 failed |
| ASR 返回 0 段 | 4A：TranscriptValidationError → TRANSCRIPT_FAILED，不落空 transcript |
| 媒体超长 | 2C：MEDIA_TOO_LONG 快速失败，不进 ffmpeg/ASR |
| 重复投递同 item | 行锁 + 状态门槛 → skipped dict |
| beat 崩溃/漏派 | 补扫任务每 600s 重扫 discovered（注记②永久兜底） |
| MinIO 不可达 | 集成测试 skipif；生产 StorageError 统一包装 |
| whisperx flag 开但未装 | warning + 回落 faster-whisper（RAD-035 硬约束） |

#### Failure Modes Registry（实现期最可能翻车点）

1. 抖音签名/风控 403（yt-dlp 提取器时效性）→ /qa 真栈验证，cookies 通道挂账不擅自加
2. HF 模型下载慢/失败 → HF_ENDPOINT 注记；CI 全 fake model 不下载
3. 60s resolve 超时套用到长下载 → 下载/取字幕独立 600s 超时（Task 6）
4. json3 坏载荷 → parse 返回 [] 走 ASR，不抛（Task 5）
5. Task 4 迁移 SQL 正则与 Python 侧不一致 → 四行断言集成测试钉死
6. 长任务持行锁 + 连接池耗尽 → 1A 低并发运维注记（README）

#### Implementation Tasks（CEO 视角摘要；全文见上方 12 个 Task）

1. pipeline_states 状态机 guard（PRD §11）→ 2. Storage 服务（MinIO/S3）→ 3. playlist 拒收 + 频道 URL 规范化 → 4. 存量 URL 补扫迁移 → 5. 字幕抓取与解析 → 6. 媒体下载 → 7. FFmpeg 标准化 → 8. TranscriptionProvider（FW + WhisperX flag）→ 9. media_asset/transcript 仓储 → 10. prepare_source_item 编排（幂等核心）→ 11. Worker 任务 + 补扫 → 12. 部署面与文档。

JSONL 工件：`~/.gstack/projects/FinanceOpinionRadar/tasks-ceo-review-20260917-080610.jsonl`

#### Completion Summary

前提证伪 0 项（P1 已在计划期前置纠正）；10 section 全审；**5 处修正已写入正文**（2A 字幕 best-effort 降级 / 2C 时长闸 / 4A 空 transcript 拒绝 / 1A 并发注记 / 9A HF 缓存卷注记），各配验证测试或文档锚点；4 项明确拒入 scope；杠杆全复用既有 EPIC-01/02 资产。**裁决：SELECTIVE EXPANSION 接受，放行 Phase 2。**

<!-- autoplan-accepted:ceo -->
- CEO-2A: fetch/parse 字幕抛 AdapterError → warning 降级 ASR，不 failed（Task 10 _subtitle_or_asr + test_subtitle_fetch_error_falls_to_asr）
- CEO-2C: prepare_max_media_duration_sec=14400 时长闸 → MediaTooLongError(code=MEDIA_TOO_LONG)（settings + Task 10 + test_over_duration_guard）
- CEO-4A: 清洗后 0 段抛 TranscriptValidationError → TRANSCRIPT_FAILED（Task 10 _persist_transcript + test_empty_transcript_rejected）
- CEO-1A: README worker 低并发指引（--concurrency=2，ASR CPU 密集 + 长期占 DB 连接）（Task 12）
- CEO-9A: README Docker HF 模型缓存卷注记（~/.cache/huggingface，~460MB）（Task 12）
  外审状态：unavailable（codex CLI 0.137.0 过旧不支持 gpt-6-astra；gpt-5.1-codex exit 124）
<!-- /autoplan-accepted:ceo -->

#### Decision Audit Trail

| ID | 决策 | 理由 | 落点 |
|---|---|---|---|
| CEO-2A | 字幕失败降级 ASR | 字幕是优化项非依赖项 | Task 10 |
| CEO-2C | 4h 时长闸 | 直播回放数小时会吃满 CPU/连接 | settings + Task 10 |
| CEO-4A | 空 transcript = 失败 | 空 transcribed 是假成功 | Task 10 |
| CEO-1A | 低并发运维注记 | 长任务持行锁占连接池 | Task 12 README |
| CEO-9A | HF 缓存卷注记 | 容器重启重下 460MB | Task 12 README |
| defer-1 | transcript GET API | 契约层归 EPIC-07 | 挂账 |
| defer-2 | 抖音 cookies 通道 | 等 /qa 403 证据 | 风险节 |
| defer-3 | status 索引 | V1 量级不需要 | 拒入 |
| defer-4 | 阶段耗时埋点 | 归 EPIC-05 pipeline_log | 挂账 |
| DX-1A | 首次 ASR 慢注记进 README | docs polish | P5 | 防"以为卡死"误判 | 不写（留困惑） |
| DX-2A | README 附 psql 验收查询 | docs polish | P5/magical moment | 结果可直接看见 | 不附（验收手写 SQL） |
| DX-3A | README 错误码速查表 | docs polish | P1 完整性 | PRD DoD"日志与错误码" | 不写（翻代码找码） |
| ENG-S0 | 复杂度触发但拒绝裁剪 | taste（gate 呈报） | P2 never-reduce | 12 任务与 RAD-030~035 一一对应 | 裁剪（丢 PRD 条目） |
| ENG-1A | 编排阶段间重锁 | mechanical | P5 explicit | commit 释锁后语义明确 | 单事务长持锁（占连接 20min） |
| ENG-2A | +2 条接线测试 | mechanical | P1 完整性 | API 400/normalize 接线有断言 | 只测函数本体 |
| ENG-3A | 下载跳过 .part | mechanical | P1 | 防半成品被当产物 | 不过滤（偶发错文件） |
| ENG-4A | TranscriptWrite 显式列映射 | mechanical | P5 explicit | **asdict(s) 传错列名必炸 | 运行时才暴露 |
| ENG-5A | 修 3 处草图缺陷 | mechanical | P3 pragmatic | 防实现照抄 bug | 留给实现期发现 |
### Phase 2.5 — DX Review（/autoplan，2026-09-17）
<!-- autoplan-baseline-edits:dx {"sourceSha256":"e54f4767ac0a9a689b82a87d63e98a4d6bae21d7b111badd2a24de00eb537d13","replacements":[{"oldText":"已完成节追加 RAD-030~035 条目；\"手工解析一个视频\"节后追加\"自动转录（EPIC-03）\"示例（`POST /source-items` 后 worker 自动 prepare；或 `celery_app.send_task` 提示）；排障表追加：ffmpeg 不存在、ASR 模型下载失败（HF_ENDPOINT）、抖音账号不可定时发现（yt-dlp 能力边界，贴单视频链接）。另加两条运维注记（CEO-1A/9A）：**worker 并发指引**——ASR 是 CPU 密集，`make worker-beat` 建议低并发（如 `--concurrency=2`），高并发只会互相抢 CPU 且长期占 DB 连接；**Docker 内 HF 模型缓存**——容器化 worker 需挂载模型缓存卷（如 `~/.cache/huggingface`），否则每次重启重新下载 ~460MB。TODO 节更新为 EPIC-04+。\n","newText":"已完成节追加 RAD-030~035 条目；\"手工解析一个视频\"节后追加\"自动转录（EPIC-03）\"示例（`POST /source-items` 后 worker 自动 prepare；或 `celery_app.send_task` 提示；示例末尾附验收查询：`docker compose exec postgres psql -U radar -d radar -c \"SELECT sequence_no, start_ms, end_ms, left(text,30) FROM transcript_segment WHERE source_item_id=<id> ORDER BY sequence_no LIMIT 5;\"`，让\"变出 transcript\"可被直接看见——DX-2A）；排障表追加：ffmpeg 不存在、ASR 模型下载失败（HF_ENDPOINT）、抖音账号不可定时发现（yt-dlp 能力边界，贴单视频链接）、**首次 ASR 启动慢属正常**（首次 transcribe 才从 HF 拉 ~460MB 模型，非卡死——DX-1A）；排障表后附**错误码速查表**（DX-3A，满足 PRD DoD\"日志与错误码\"）：`RESOLVE_FAILED`（resolve 阶段失败→重试或查链接）/`DOWNLOAD_FAILED`（下载失败→网络或风控）/`MEDIA_FFMPEG_FAILED`（标准化失败→查 ffmpeg 二进制与源文件）/`ASR_FAILED`（转录失败→查模型/资源）/`TRANSCRIPT_FAILED`（段落校验失败→看 last_error.message）/`MEDIA_TOO_LONG`（超 prepare_max_media_duration_sec→拆条或调大阈值），均读自 `source_item.metadata_json.last_error`。另加两条运维注记（CEO-1A/9A）：**worker 并发指引**——ASR 是 CPU 密集，`make worker-beat` 建议低并发（如 `--concurrency=2`），高并发只会互相抢 CPU 且长期占 DB 连接；**Docker 内 HF 模型缓存**——容器化 worker 需挂载模型缓存卷（如 `~/.cache/huggingface`），否则每次重启重新下载 ~460MB。TODO 节更新为 EPIC-04+。\n"}]} -->

**Snapshot:** `autoplan-dx-UjaARj/dx-implementation.md`（sha `e54f4767…`，含 CEO 修正后的正文）。Phase 2 skipped（no UI scope）。
**Voices:** Codex = **unavailable**（沿用本会话结论：CLI 0.137.0 不支持 gpt-6-astra；gpt-5.1-codex exit 124。修复：升级 CLI 或设 `GSTACK_CODEX_MODEL`）。Claude = **in-host native pass**（用户约定不派 subagent）。Consensus = **N/A**（单通道；无 CONFIRMED 项）。

**Mode:** DX POLISH（dx-phase 覆写规则）。**Product type:** 后端管线模块（repo 内部运维面 = README/Make/.env/日志错误码），非对外 SDK/API 产品 → Pass 7 社区面按内部仓库处理。

#### Developer Persona（0A，依 README/Makefile 证据推断）

```
TARGET DEVELOPER PERSONA
========================
Who:       本仓库唯一维护者（后端工程师），兼职运维；未来可能有第二贡献者
Context:   本机 docker compose 起栈，贴一条抖音链接验证管线，跑 /qa 真栈演示
Tolerance: 排障 >10 分钟就会翻日志猜；希望错误码 + README 排障表直接给答案
Expects:   make 命令链开箱可用；.env 全默认；失败能从 metadata_json.last_error 读出原因
```

#### DX DUAL VOICES — CONSENSUS TABLE

```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Getting started < 5 min?          7/10    N/A     single-voice
  2. API/CLI naming guessable?         7/10    N/A     single-voice
  3. Error messages actionable?        8/10    N/A     single-voice
  4. Docs findable & complete?         7/10    N/A     single-voice
  5. Upgrade path safe?                8/10    N/A     single-voice
  6. Dev environment friction-free?    8/10    N/A     single-voice
═══════════════════════════════════════════════════════════════
CONFIRMED = native + outside agree; primary cannot replace outside.
Missing/disabled voice = N/A, never CONFIRMED. 无单通道 critical 发现。
```

#### Pass 1-8 裁决（POLISH）

| Pass | 分 | 发现 → 裁决 |
|---|---|---|
| 1 Getting Started | 7 | 首次 ASR 静默拉 460MB 模型，README 只写"下载失败"没写"慢属正常" → **DX-1A 已修**（排障表补注记）。扣分余项：模型下载时间本身（HF_ENDPOINT 已覆盖） |
| 2 API/CLI | 7 | 命名沿用 EPIC-02 惯例（guessable）；transcript 读取 API defer EPIC-07（已记录）；验收要手写 SQL → **DX-2A 已修**（README 示例附 psql 验收查询） |
| 3 Error Messages | 8 | `last_error={code,stage,message,at}` 结构化好；但 6 个错误码散在 Task 10 正文，README 无速查表 → **DX-3A 已修**（错误码表，满足 PRD DoD"日志与错误码"） |
| 4 Documentation | 7 | 排障表/示例随 1A/2A/3A 补齐；.env.example 注释完整 |
| 5 Upgrade Path | 8 | Alembic 幂等 backfill、SubtitleResult 新字段带默认值、无破坏性契约变更；模型升级 = 改 ASR_MODEL_NAME 重跑（TRANSCRIBE 幂等键含 model） |
| 6 Dev Environment | 8 | FAKE 二进制注入测试不下载模型；集成测试 skipif；容器 ffmpeg + HF 缓存卷注记 |
| 7 Community | N/A | 内部单人仓库，无社区面；不做任务（YAGNI） |
| 8 DX Measurement | 5 | 无 TTHW 埋点/漏斗分析；内部工具，验收 = /qa 真栈演示（已在计划验收节定义）→ 记 gap 不加任务（EPIC-09 质量评估再议） |

#### Developer Journey Map（9 段）

| Stage | Developer does | Friction | Status |
|---|---|---|---|
| 1 Discover | 读 README 已完成节 | — | ok |
| 2 Install | `docker compose up -d` + `make migrate` | ffmpeg 进镜像自动 | ok |
| 3 Configure | .env 全默认，零配置 | — | ok |
| 4 Hello World | resolve-url → POST /source-items → worker | 看不到 transcript 结果 | fixed（DX-2A psql 查询示例） |
| 5 Real Usage | 批量贴链接 + beat 补扫 | — | ok |
| 6 Debug | 查 last_error + README 排障表 | 错误码无总表 | fixed（DX-3A） |
| 7 Observe | status 字段推进 + 日志 | — | ok |
| 8 Upgrade | alembic upgrade / 换模型 | 幂等已保证 | ok |
| 9 Scale | 提并发/挂缓存卷 | 误开高并发 | fixed（CEO-1A/9A 注记） |

#### Empathy narrative（节选，first-person）

我贴了一条抖音链接，`POST /source-items` 返回 201。然后呢？什么都没发生——worker 在后台跑，我不知道去哪看结果。我猜要查库，但 transcript_segment 的表结构我没背过……（DX-2A 后：README 示例末尾就有那条 psql 查询，复制粘贴即见带时间戳的中文段落。）第一次跑等了十分钟没动静，以为卡死了，其实在下 460MB 模型（DX-1A 后：排障表写明"首次慢属正常"）。

#### TTHW assessment

Current ~8 min（compose 起栈 2 min + migrate/seed 1 min + 首次模型下载 3-6 min 主导）→ target 5 min（HF_ENDPOINT 镜像 + 缓存卷后复跑 <5 min）。Competitive tier：内部工具，无外部竞品基准，采用参考基准（Docker 5 min 档）。

#### DX Scorecard

| Dimension | Score |
|---|---|
| Getting Started | 7/10 |
| API/CLI | 7/10 |
| Error Messages | 8/10 |
| Documentation | 8/10（修后） |
| Upgrade Path | 8/10 |
| Dev Environment | 8/10 |
| Community | N/A（内部仓库） |
| DX Measurement | 5/10（gap 记录） |
| **Overall** | **7/10** |

**TTHW:** 8 min → 5 min（target）。**Magical moment:** 贴链接 → psql 查询即见中文带时间戳段落（delivery vehicle = copy-paste demo command，DX-2A 落地）。

#### NOT in scope（DX 视角）

- transcript 读取 API → EPIC-07；TTHW/漏斗埋点 → 内部工具不做；社区建设 → 非开源仓库。

#### Completion Summary

3 项修正已写入 Task 12（DX-1A 首次慢注记 / DX-2A psql 验收查询 / DX-3A 错误码速查表），1 处编辑。2 项 gap 记录不加任务（DX Measurement、Community）。**裁决：DX POLISH 接受，放行 Phase 3。**

<!-- autoplan-accepted:dx -->
- DX-1A: README 排障表补"首次 ASR 启动慢属正常"注记（首次 transcribe 才从 HF 拉 ~460MB 模型）（Task 12 Step 3）
- DX-2A: README"自动转录"示例末尾附 psql 验收查询（transcript_segment 可直接看见）（Task 12 Step 3）
- DX-3A: README 排障表后附错误码速查表（RESOLVE_FAILED/DOWNLOAD_FAILED/MEDIA_FFMPEG_FAILED/ASR_FAILED/TRANSCRIPT_FAILED/MEDIA_TOO_LONG + 读自 metadata_json.last_error）（Task 12 Step 3）
<!-- /autoplan-accepted:dx -->
### Phase 3 — Eng Review（/autoplan，2026-09-17，required gate）
<!-- autoplan-baseline-edits:eng {"sourceSha256":"63cf6c55578d17e93540df4b323ade5c4c5ddd5f5e82e8974e9133a63aa2436e","replacements":[{"oldText":"        adapter.resolve(\"https://www.youtube.com/watch?v=ok\")  # FAKE 二进制返回 playlist 载荷\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n","newText":"        adapter.resolve(\"https://www.youtube.com/watch?v=ok\")  # FAKE 二进制返回 playlist 载荷\n```\n\nENG-2A 补两条（断 API/编排接线，不止函数本体）：\n\n```python\n# test_api_source_items.py 追加 —— fake adapter 抛 NotSingleItemError → 400（不是 500/504）\ndef test_resolve_url_playlist_returns_400(...): ...\n\n# test_upsert_refresh_semantics.py（或 discovery 集成测试）追加 —— 注册侧 normalize 接线生效\ndef test_manual_registration_normalizes_channel_url(...):  # channel_url 裸地址入库后 source_account.url 已带 /videos\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n"},{"oldText":"    assert result is not None and result.language == \"zh-Hans\" and result.fmt == \"json3\" and b\"A\" in result.content or result.content == JSON3.encode()\n","newText":"    assert result is not None and result.language == \"zh-Hans\" and result.fmt == \"json3\"  # ENG-5A：断言用全等，不用 or 短路糊弄\n    assert result.content == JSON3.encode()\n"},{"oldText":"        files = [f for f in Path(workdir).iterdir() if f.is_file()]\n","newText":"        files = [f for f in Path(workdir).iterdir() if f.is_file() and f.suffix != \".part\"]  # ENG-3A：跳过 yt-dlp 半成品残留\n"},{"oldText":" PROVIDER = \"faster-whisper\"\n","newText":"PROVIDER = \"faster-whisper\"\n"},{"oldText":"            diarize = whisperx.DiarizationPipeline(); labels = diarize(audio)\n            result = whisperx.assign_word_speakers(diarize(audio), result)\n","newText":"            diarize = whisperx.DiarizationPipeline(); labels = diarize(audio)  # ENG-5A：只调一次，labels 复用\n            result = whisperx.assign_word_speakers(labels, result)\n"},{"oldText":"            self._session.add(TranscriptSegment(source_item_id=source_item_id, sequence_no=seq, **asdict(s)))\n","newText":"            # ENG-4A：模型列名是 asr_confidence/speaker_label，TranscriptWrite 字段是 confidence/speaker——显式映射，禁止 **asdict(s)（会传错列名直接 TypeError）\n            self._session.add(TranscriptSegment(source_item_id=source_item_id, sequence_no=seq,\n                                                start_ms=s.start_ms, end_ms=s.end_ms, text=s.text,\n                                                asr_confidence=s.confidence, language=s.language, speaker_label=s.speaker))\n"},{"oldText":"- `_lock_item`：`session.execute(select(SourceItem).where(...).with_for_update())`。\n","newText":"- `_lock_item`：`session.execute(select(SourceItem).where(...).with_for_update())`。**ENG-1A 阶段间重锁**：commit 会释放行锁——中途状态落库后，下一阶段开始时必须重取行（再次 `SELECT … FOR UPDATE` + 状态门槛复核）再推进；既不长期占锁/占连接（配合 CEO-1A 低并发），又保证跨阶段无双进（状态门槛拦住并发派发）。\n"}]} -->

**Snapshot:** `autoplan-eng-Qq3kF1/eng-implementation.md`（sha `63cf6c55…`，含 CEO+DX 修正后正文）。代码核对源：`apps/api/app/db/models/media.py`、`services/media/adapters/yt_dlp.py:102-117`、`services/discovery.py:130`、`worker/celery_app.py`、`core/settings.py:21-32`、`migrations/versions/274c026452c4_*`。
**Voices:** Codex = **unavailable**（CLI 0.137.0 model_unusable，本会话已双次实测）。Claude = **in-host native pass**（用户约定不派 subagent）。Consensus = N/A（单通道，无 CONFIRMED 项）。

#### ENG DUAL VOICES — CONSENSUS TABLE

```
  Dimension                           Claude  Codex  Consensus
  1. Architecture sound?               是      N/A     single-voice
  2. Test coverage sufficient?         是(+2)  N/A     single-voice
  3. Performance risks addressed?      是      N/A     single-voice
  4. Security threats covered?         是      N/A     single-voice
  5. Error paths handled?              是(+1)  N/A     single-voice
  6. Deployment risk manageable?       是      N/A     single-voice
```

#### Step 0 — Scope Challenge（override: never reduce）

- **已有代码覆盖**：EPIC-01 全部表（含 media_asset/transcript_segment 列齐）、EPIC-02 adapter/contracts/url_guard/discovery/worker 骨架——计划全部复用，无平行重建（What-already-exists 见 CEO 记录）。
- **复杂度检查触发**（12 任务、新增 3 个服务包）：**判定为合理**——文件数与 PRD RAD-030~035 一一对应（storage/audio/transcription 各自是 PRD 点名的独立服务），无投机性抽象；拒绝裁剪（eng-phase P2 override）。Phase 4 gate 呈报。
- **Layer 标注**：boto3 [Layer1]、faster-whisper [Layer1]、FFmpeg subprocess [Layer1]、json3 解析 [Layer3-必要]（无标准库/已装依赖覆盖）、状态机 guard [Layer3-必要]（PRD §11 唯一权威）。无 EUREKA。
- **Completeness**：非捷径方案（CEO-2A/2C/4A 已把失败路径补全）。**Distribution**：Dockerfile.api 已有 CI docker-build job，ffmpeg 进镜像即分发型，无缺口。
- **TODOS cross-ref**：仓库约定不建 TODOS.md（Plan #1 决定）；deferred 项统一记执行计划注记/本计划 NOT-in-scope。

#### Section 1 — Architecture

```
                     ┌────────────────────────── worker (celery) ──────────────────────────┐
                     │  prepare_source_item(item_id)      dispatch_pending_prepares (beat)  │
                     │        │                                     │ list_by_status('discovered')│
                     │        ▼                                     └──── send_task ×N ──┐ │
                     │  preparation.py（编排，行锁+状态门槛，阶段间重锁 ENG-1A）            │ │
                     │   ├─ adapter.resolve ──► yt_dlp.py 子进程（url_guard 入口）  ◄──────┘ │
                     │   ├─ fetch_subtitle ─► subtitles.py parse/is_usable                  │
                     │   │      └ 不可用/抛错(CEO-2A) ─► download_media ─► audio.py ffmpeg   │
                     │   │                                          └─► wav ─► transcription/  │
                     │   │                                     faster_whisper | whisperx(flag) │
                     │   ├─ storage/ (MinioStorage: put/signed/exists/delete, 禁直调 boto3) │
                     │   └─ repositories/ media_assets.py + transcripts.py (replace 语义)   │
                     └──────────────────────────────────────────────────────────────────────┘
                       API: resolve-url / source_items（NotSingleItemError→400，ENG-2A 用例）
                       状态机: domain/pipeline_states.py guard（PRD §11）
```

- **耦合**：编排依赖 contracts Protocol（注入 fake 即测），实现依赖方向单向（services→repositories→models）。无环。
- **ENG-1A（发现）**：`_lock_item` 单点锁 + 中途 commit 存在"锁早释"细节——commit 释放行锁后若不重锁，后续阶段在无锁状态推进，幂等门槛（仅 discovered/failed 可进）虽拦住新派发，但跨阶段重入语义不明。**修正**：Task 10 注记"阶段间重锁"（每阶段重取 FOR UPDATE + 门槛复核），不长期占锁/占连接（呼应 CEO-1A），双进仍被拦。
- **失败场景**（每条新 codepath 一条）：见 Failure Modes Registry。
- **单一 SPOF**：单 worker 进程 + CPU ASR——V1 接受（低并发注记），扩展点在 queue 拆分（EPIC-10 RAD-101）。

#### Section 2 — Code Quality

- **ENG-4A（P1 发现，confidence 9/10）**：Task 9 草图 `TranscriptSegment(**asdict(s))` 会把 `confidence=`/`speaker=` 传给模型——实际列名 `asr_confidence`/`speaker_label`（media.py:34,40），TypeError on flush。**修正**：显式字段映射（已改）。验证：集成测试 test_replace_for_item_swaps_atomically 落库即炸，修正后绿。
- **ENG-5A（P3，confidence 10/10，预知草图缺陷收口）**：Task 5 断言 `… or result.content == …` 恒真短路 → 拆两行全等断言；Task 8 ` PROVIDER` 行首空格 SyntaxError；whisperx sketch `diarize(audio)` 重复调用 → 复用 labels。均已改。
- **DRY**：错误码→`_STAGE_CODES` 单点；fake 注入模式沿用 EPIC-02 harness；无复制粘贴漂移。命名与既有 `MediaSourceAdapter`/`SourceItemRepository` 一致。

#### Section 3 — Test Review（不可压缩）

```
CODE PATHS                                              覆盖
[+] pipeline_states.ensure_transition                    5 unit（含终态/未知状态）★★★
[+] MinioStorage put/signed/exists/delete/ensure/wrap    6 unit(FakeClient) + MinIO 集成(skipif) ★★★
[+] normalize_channel_url                                11 parametrize（4 裸地址/5 页签/2 直通）★★★
[+] _parse_resolved playlist 拒收                        1 unit + [GAP→ENG-2A] API 400 接线用例
[+] discovery 注册 normalize 接线                        [GAP→ENG-2A] upsert 断言用例
[+] backfill 迁移                                        4 行断言 + 幂等 + test_migrations 回归 ★★★
[+] fetch_subtitle bytes/missing/auto-flag               3 unit（FAKE 写文件 harness）★★★
[+] parse_subtitle json3/vtt/短/坏                       4 unit ★★★
[+] download_media 文件/空产出/args/.part 跳过(ENG-3A)   3+1 unit ★★★
[+] normalize_audio sha/duration/非零退出/二进制缺失     3 unit（FAKE ffmpeg 写真 wav）★★★
[+] FasterWhisperProvider 映射/错误包装/惰性加载        2 unit（monkeypatch WhisperModel）★★★
[+] whisperx flag 关/开未装回落/开且装/diarize           4 unit（sys.modules 假模块）★★★
[+] MediaAsset/Transcript 仓储 record/list/replace/回滚  3 integration（真 PG）★★★
[+] preparation 10 场景（字幕/ASR/降级/空/超长/重复/失败/回填/中间态）integration ★★★
[+] worker registry/pipeline/sweep/批量截断              2+2 integration+unit ★★★
USER FLOWS
[+] 贴链接→transcribed 全链路                            integration + 真栈验收 [→E2E /qa]
[+] 重复投递→skipped                                     test_duplicate_dispatch_skips ★★★
[+] 孤儿 discovered→补扫接管                              test_sweep_dispatches_discovered ★★★
[+] 失败→failed→重跑恢复                                 test_failure_records_error_code ★★★
LLM integration：无 prompt 改动（EPIC-04 才引入）→ 无 [→EVAL] 项
COVERAGE: 计划内 codepath 100% 有对应测试；2 个接线 GAP 已由 ENG-2A 补进计划
```

- **REGRESSION 检查**：`_parse_resolved` 行为变更（拒 playlist）——既有 resolve 测试全量回归（Task 3 Step 4 显式跑）；存量 URL 迁移有幂等谓词 + 二次 upgrade 断言。无未守护回归。

#### Section 4 — Performance

- 无 N+1（仓储均按 item_id 单查；补扫单 SELECT+LIMIT）。
- ASR CPU 密集 + 模型惰性加载（worker 冷启动不炸）——低并发注记 + HF 缓存卷（CEO-1A/9A）。
- wav 16k mono = 32KB/s（1h≈115MB 磁盘，TemporaryDirectory 用完即焚）；sha256 流式 1MB chunk；上传 boto3 multipart 自动。
- `replace_for_item` delete+insert 逐段 add——段数数百级，单事务可接受；不为 V1 上 bulk（YAGNI，记 NOT-in-scope）。
- 补扫无索引 seq scan——V1 量级 <1e4 行（defer-3 已裁决）。

#### NOT in scope（Eng 视角新增）

- transcript bulk insert 优化（段数数百级不值得）
- queue 拆分 media/asr/llm → EPIC-10 RAD-101
- 存量 discovered 一次性回填脚本 → 补扫任务本身即兜底（注记②）

#### Failure Modes Registry（critical gap 标记）

| # | 失败模式 | 测试 | 错误处理 | 用户可见性 |
|---|---|---|---|---|
| FM1 | 阶段间锁早释→语义不明 | test_intermediate_status_persisted（部分） | ENG-1A 重锁 | 日志+状态字段 ✓ |
| FM2 | yt-dlp .part 残留被当产物 | ENG-3A 用例 | suffix 过滤 | -（防患）✓ |
| FM3 | TranscriptWrite 列名不匹配 | test_replace_for_item（落库即炸） | ENG-4A 映射 | -✓ |
| FM4 | 字幕抓取抛错 | test_subtitle_fetch_error_falls_to_asr | CEO-2A 降级 | warning ✓ |
| FM5 | ASR 空 transcript | test_empty_transcript_rejected | CEO-4A | TRANSCRIPT_FAILED ✓ |
| FM6 | 超长媒体 | test_over_duration_guard | CEO-2C | MEDIA_TOO_LONG ✓ |
| FM7 | 抖音风控 403 | /qa 真栈验证 | 挂账 cookies 通道 | DOWNLOAD_FAILED ✓ |
| FM8 | MinIO 不可达 | 集成 skipif + 单测 wrap | StorageError | last_error ✓ |
| FM9 | beat/投递丢失 | test_sweep_dispatches | 补扫兜底 | 状态滞留可见 ✓ |
| FM10 | 模型下载慢/失败 | CI 全 fake 不触发 | HF_ENDPOINT 注记 | 首次慢注记（DX-1A）✓ |

**Critical gaps（无测试+无处理+静默）：0。**

#### Worktree parallelization

Sequential implementation, no parallelization opportunity（用户既定约定：主会话逐任务串行；且 Task 2-11 共享 contracts/settings/仓储目录，并行只会制造冲突）。

#### Implementation Tasks（Eng 汇总，含前序相位任务合并执行）

全部并入计划 12 任务执行（ENG-1A~5A 已写进对应 Task）；无独立新增任务文件。

#### Completion Summary

- Step 0: Scope Challenge — scope accepted as-is（复杂度触发但 PRD 对应合理，never-reduce override）
- Architecture Review: 1 issue（ENG-1A 阶段间重锁，已修）
- Code Quality Review: 2 issues（ENG-4A 列名映射 P1、ENG-5A 草图缺陷×3，已修）
- Test Review: diagram produced, 2 gaps identified（ENG-2A 已补进计划）
- Performance Review: 0 blocking issues（3 项 YAGNI 记 NOT-in-scope）
- NOT in scope: written；What already exists: written（CEO 记录）
- TODOS.md updates: 0 proposed（仓库约定无 TODOS.md，deferred 记执行计划注记）
- Failure modes: 10 登记，0 critical gaps
- Outside voice: codex unavailable（本会话第 3 次确认）
- Parallelization: sequential（既定约定）
- Lake Score: N/A（无 complete-vs-shortcut 抉择）
- Unresolved decisions: 0（全部自动裁决并在 Phase 4 gate 呈报）

<!-- autoplan-accepted:eng -->
- ENG-1A: Task 10 编排加"阶段间重锁"注记——commit 释放行锁后每阶段重取 FOR UPDATE + 状态门槛复核，不长期占锁且跨阶段无双进
- ENG-2A: Task 3 补 2 条接线测试——API NotSingleItemError→400 用例（test_api_source_items.py）+ 注册侧 normalize 生效断言（upsert 集成测试）
- ENG-3A: Task 6 download_media 文件扫描跳过 .part 残留（yt-dlp 半成品）
- ENG-4A: Task 9 TranscriptWrite→TranscriptSegment 显式字段映射（confidence→asr_confidence、speaker→speaker_label），禁 **asdict(s)
- ENG-5A: Task 5 断言改全等（拆两行）；Task 8 修 PROVIDER 行首空格；whisperx sketch 复用 diarize labels 不重复调用
<!-- /autoplan-accepted:eng -->
### Cross-Phase Themes（跨相位主题）

| Theme | 出现相位 | 信号强度 |
|---|---|---|
| 长任务资源占用（CPU/DB 连接/锁） | CEO-1A、CEO-2C、ENG-1A | 高置信：三相独立提出，收敛为"低并发注记 + 时长闸 + 阶段间重锁"三件套 |
| 模型下载与缓存（460MB/HF） | CEO-9A、DX-1A | 高置信：运维注记 + 首次慢提示 + HF_ENDPOINT 兜底 |
| 抖音能力边界与风控 | CEO P1/P3、DX journey、ENG FM7 | 中置信（待 /qa 真栈证实）：能力边界已实测，风控 403 为挂账风险 |

### Pre-Gate Verification（/autoplan 自检）

- CEO：premise challenge 6 条具名 ✓｜10 section findings ✓｜Error&Rescue ✓｜Failure Modes ✓｜NOT-in-scope ✓｜What-exists ✓｜dream delta ✓｜Completion Summary ✓｜dual voices（codex unavailable + in-host）✓｜consensus 表 ✓
- Design：skipped（no UI scope）✓（相位记录：Phase 2 skipped）
- DX：8 维评分 ✓｜journey map（9 段）✓｜empathy narrative ✓｜TTHW 8→5min ✓｜checklist 并入计划 Task 12 ✓｜dual voices ✓｜consensus 表 ✓
- Eng：scope challenge 含实码核对（media.py/yt_dlp.py/discovery.py/settings.py/celery_app.py/migrations）✓｜ASCII 架构图 ✓｜test diagram ✓｜test plan 工件落盘 `mshengran-main-eng-review-test-plan-20260917-083000.md` ✓｜NOT-in-scope ✓｜What-exists（CEO 记录）✓｜failure modes 10 条 0 critical ✓｜Completion Summary ✓｜dual voices ✓｜consensus 表 ✓
- Cross-phase themes ✓｜Decision Audit Trail 19 行 ✓
## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` via /autoplan | Scope & strategy | 1 | CLEAR | 6 proposals, 5 accepted (CEO-1A/2A/2C/4A/9A), 4 deferred |
| Outside Review | codex via /autoplan | Independent 2nd opinion | 3 phases | unavailable | codex CLI 0.137.0 model_unusable（gpt-6-astra 拒版 / gpt-5.1-codex 124 超时）；单通道 in-host 全审 |
| Eng Review | `/plan-eng-review` via /autoplan | Architecture & tests (required) | 1 | CLEAR | 4 issues found, 0 critical gaps（ENG-1A/2A/3A/4A/5A 全部折入计划） |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | skipped | no UI scope detected |
| DX Review | `/plan-devex-review` via /autoplan | Developer experience gaps | 1 | CLEAR | score 7/10 → 8/10（修后），TTHW 8min → 5min，3 fixes (DX-1A/2A/3A) |

- **OUTSIDE COVERAGE:** provider=codex, phases ceo/dx/eng=unavailable（CLI 过旧不支持所选模型；修复=升级 @openai/codex 或设 GSTACK_CODEX_MODEL）, design=skipped。native in-host 全相位完成（用户既定不派 subagent 约定）。
- **VERDICT:** CEO + ENG + DX CLEARED — ready to implement（待用户 Phase 4 批准）。

NO UNRESOLVED DECISIONS

---

## Amendment（Task 10 后追加）：Task 10.5 吸收 voice-pro 实战经验

**来源**：用户指定参考 `/Users/mshengran/Project/voice-pro/abus-aikorea-voice-pro`（同为 yt-dlp + faster-whisper 栈的成熟项目）。

**采纳 3 项**（均已 TDD 落地）：
1. **yt-dlp 反 SABR 403**（来源 `abus_downloader.py:73-87`）：YouTube 对多数客户端启用 SABR-only 流，
   默认客户端无 JS runtime 时退化到废弃路径吃 HTTP 403。所有 yt-dlp 调用统一追加
   `--extractor-args youtube:player_client=android`（对非 YouTube 平台是无害 no-op）。
   JS runtime（deno/node）由 worker 环境自备，README 注记。
2. **ASR 幻觉段防护**（来源 `abus_asr_faster_whisper.py:200-217` + `abus_asr_parameters.py`）：
   - 段末时间戳 > 音频时长 × `asr_max_segment_end_ratio`(默认 1.05) → 丢弃并截断，
     防长静音段幻觉漂移导致迭代器永不结束；新增 settings 键并穿参 provider/工厂。
   - `condition_on_previous_text=False`：切断上一段文本条件影响，抑制幻觉连锁。
3. **模型缓存共用**：voice-pro `model/faster-whisper/`（Systran CTranslate2 布局）symlink 进
   `~/.cache/huggingface/hub/`（base/medium/medium.en/large-v3 四个），
   Radar 零代码；`asr_model_name` 默认 small→**medium**（共用缓存无 small；中文财经内容效果更好）。

**核实不采纳**：
- voice-pro 字幕提取仅覆盖本地文件转写，Radar 的 URL 字幕抓取（json3/vtt）已覆盖且更强。
- mlx-community whisper（Apple Silicon 专用）不适用于 Docker/linux 部署面。
- silero VAD 在 faster-whisper 1.2.1 为内置资产（`get_assets_path()`），无需下载/共用。

**测试**：unit +6（settings 默认档 / resolve·fetch·download argv 携带 SABR 参数 / 幻觉段丢弃 / 反幻觉 kwargs）；
fake_ytdlp fixture 的 argv dump 提升为全行为可用（原先仅 writeout 分支）。
