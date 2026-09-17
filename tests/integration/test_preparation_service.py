"""prepare_source_item 编排集成测试（RAD-031）：真 DB，外部依赖全 fake。

覆盖：字幕优先路 / ASR 兜底路 / CEO-2A 字幕降级 / CEO-2C 时长闸 / CEO-4A 空段落 /
幂等跳过 / 失败记录与恢复 / enrich 只补空 / 中途状态落库（ENG-1A 重锁前提）。
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.db.models import MediaAsset, SourceItem
from app.repositories.source_items import SourceItemRepository
from app.services import preparation
from app.services.media.audio import NormalizedAudio
from app.services.media.contracts import (
    AdapterProcessError,
    DownloadResult,
    ResolvedMedia,
    SubtitleResult,
    SubtitleTrack,
)
from app.services.transcription.contracts import (
    TranscriptionError,
    TranscriptResult,
    TranscriptSegmentResult,
)

from tests.integration.test_discover_tasks import _make_account

URL = "https://www.youtube.com/watch?v=abc123"
JSON3 = (
    '{"events":[{"tStartMs":0,"dDurationMs":1500,'
    '"segs":[{"utf8":"今天"},{"utf8":"A股"},{"utf8":"大涨，"},{"utf8":"美联储加息"}]}]}'
)


class FakeAdapter:
    def __init__(
        self,
        resolved: ResolvedMedia,
        *,
        subtitle: SubtitleResult | None = None,
        subtitle_error: Exception | None = None,
    ) -> None:
        self._resolved = resolved
        self._subtitle = subtitle
        self._subtitle_error = subtitle_error
        self.download_calls = 0

    def resolve(self, url: str) -> ResolvedMedia:
        return self._resolved

    def discover(self, account):  # pragma: no cover - 编排不应触发
        raise AssertionError("prepare 不应触发 discover")

    def fetch_subtitle(self, item, language=None, auto=False):
        if self._subtitle_error is not None:
            raise self._subtitle_error
        return self._subtitle

    def download_media(self, item, workdir):
        self.download_calls += 1
        p = Path(workdir) / "abc123.m4a"
        p.write_bytes(b"fake-audio")
        return DownloadResult(local_path=str(p), size_bytes=p.stat().st_size)


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_file(self, key, path, *, content_type=None):
        self.objects[key] = Path(path).read_bytes()
        return f"s3://fake/{key}"

    def get_signed_url(self, key, *, expires_sec=3600):
        return f"https://sig/{key}"

    def exists(self, key):
        return key in self.objects

    def delete(self, key):
        self.objects.pop(key, None)


class FakeProvider:
    provider = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.segments: tuple[TranscriptSegmentResult, ...] = (
            TranscriptSegmentResult(0, 1500, "今天 A股大涨", confidence=0.9),
        )
        self.error: Exception | None = None
        self.status_probe = None  # transcribe 时刻的 DB 状态探针
        self.probed_status: str | None = None

    def transcribe(self, path, language=None):
        self.calls.append(path)
        if self.status_probe is not None:
            self.probed_status = self.status_probe()
        if self.error is not None:
            raise self.error
        return TranscriptResult(
            language="zh", provider="fake", model="tiny", segments=self.segments
        )


def _resolved(**over) -> ResolvedMedia:
    base: dict = {
        "platform": "youtube",
        "external_item_id": "abc123",
        "title": "富标题",
        "canonical_url": URL,
        "thumbnail_url": None,
        "duration_ms": 1250500,
        "item_type": "vod",
        "published_at": datetime(2026, 3, 15, tzinfo=UTC),
        "channel_external_id": "ch_42",
        "channel_name": "宏观日记",
        "subtitles": (),
        "metadata": {},
        "channel_url": "https://www.youtube.com/@macro-diary",
    }
    base.update(over)
    return ResolvedMedia(**base)


def _make_discovered_item(db_session, external_id: str = "abc123"):
    account = _make_account(db_session, external_id=f"acc_{external_id}")
    db_session.flush()
    item, _created = SourceItemRepository(db_session).upsert_by_external(
        source_account_id=account.id,
        external_item_id=external_id,
        title="旧标题",
        canonical_url=URL,
        item_type="vod",
    )
    db_session.flush()
    return item


@pytest.fixture
def fakes(db_session, monkeypatch: pytest.MonkeyPatch):
    storage = FakeStorage()
    provider = FakeProvider()
    holder: dict = {"item_id": None}
    # ENG-1A 探针：provider 运行时读 DB 状态，验证中途状态已 commit
    provider.status_probe = lambda: db_session.get(SourceItem, holder["item_id"]).status
    adapter = FakeAdapter(_resolved())

    def _fake_normalize(input_path, output_dir, *, binary="ffmpeg", timeout_sec=600):
        out = Path(output_dir) / "abc123.16k-mono.wav"
        out.write_bytes(b"RIFF-fake-wav")
        return NormalizedAudio(
            path=out, input_sha256="i" * 64, output_sha256="o" * 64, duration_ms=1250
        )

    monkeypatch.setattr(preparation, "normalize_audio", _fake_normalize)
    return SimpleNamespace(
        adapter=adapter, storage=storage, provider=provider, holder=holder
    )


@pytest.fixture
def prepared(db_session, fakes):
    def _run(adapter=None, provider=None):
        item = _make_discovered_item(db_session)
        fakes.holder["item_id"] = item.id
        return item, preparation.prepare_source_item(
            item.id,
            db_session,
            adapter or fakes.adapter,
            fakes.storage,
            provider or fakes.provider,
        )

    return _run


def test_subtitle_path(db_session, fakes, prepared) -> None:
    fakes.adapter = FakeAdapter(
        _resolved(subtitles=(SubtitleTrack("zh-Hans", False),)),
        subtitle=SubtitleResult(language="zh-Hans", content=JSON3.encode(), fmt="json3"),
    )
    item, result = prepared(adapter=fakes.adapter)
    assert result["status"] == "transcribed" and result["origin"] == "subtitle"
    assert item.status == "transcribed"
    assert fakes.provider.calls == []  # 字幕可用，不碰 ASR
    assert fakes.adapter.download_calls == 0
    assets = db_session.query(MediaAsset).filter_by(source_item_id=item.id).all()
    assert {a.asset_type for a in assets} == {"subtitle", "transcript"}
    assert any(k.startswith("subtitles/") for k in fakes.storage.objects)


def test_asr_path_when_no_subtitle(db_session, fakes, prepared) -> None:
    item, result = prepared()
    assert result["status"] == "transcribed" and result["origin"] == "asr"
    assert item.status == "transcribed"
    assert len(fakes.provider.calls) == 1
    assert fakes.provider.calls[0].endswith(".wav")
    assets = db_session.query(MediaAsset).filter_by(source_item_id=item.id).all()
    assert {a.asset_type for a in assets} == {"audio", "transcript"}
    audio = next(a for a in assets if a.asset_type == "audio")
    assert audio.sha256 == "o" * 64 and audio.duration_ms == 1250
    transcript_meta = item.metadata_json["transcript"]
    assert transcript_meta["provider"] == "fake"
    assert transcript_meta["media_sha256"] == "o" * 64
    assert transcript_meta["segment_count"] == 1
    assert any(k.startswith("transcripts/") for k in fakes.storage.objects)


def test_asr_path_when_subtitle_unusable(db_session, fakes, prepared) -> None:
    # 字幕解析后总字符 < min_chars → 降级 ASR
    fakes.adapter = FakeAdapter(
        _resolved(subtitles=(SubtitleTrack("zh-Hans", False),)),
        subtitle=SubtitleResult(
            language="zh-Hans",
            content=b'{"events":[{"tStartMs":0,"dDurationMs":100,'
            b'"segs":[{"utf8":"hi"}]}]}',
            fmt="json3",
        ),
    )
    _item, result = prepared(adapter=fakes.adapter)
    assert result["origin"] == "asr"
    assert fakes.adapter.download_calls == 1


def test_subtitle_fetch_error_falls_to_asr(db_session, fakes, prepared) -> None:
    # CEO-2A：字幕是 best-effort，fetch 抛错降级 ASR，不 failed
    fakes.adapter = FakeAdapter(
        _resolved(subtitles=(SubtitleTrack("zh-Hans", False),)),
        subtitle_error=AdapterProcessError("yt-dlp 退出码 1: 403"),
    )
    item, result = prepared(adapter=fakes.adapter)
    assert result["status"] == "transcribed" and result["origin"] == "asr"
    assert item.status == "transcribed"


def test_empty_transcript_rejected(db_session, fakes, prepared) -> None:
    # CEO-4A：0 段 transcript 是失败不是成功
    fakes.provider.segments = ()
    item, _result = prepared()
    assert item.status == "failed"
    assert item.metadata_json["last_error"]["code"] == "TRANSCRIPT_FAILED"


def test_over_duration_guard(db_session, fakes, prepared) -> None:
    # CEO-2C：超 prepare_max_media_duration_sec 快速失败，不进下载/ASR
    fakes.adapter = FakeAdapter(_resolved(duration_ms=15_000_000))
    item, _result = prepared(adapter=fakes.adapter)
    assert item.status == "failed"
    assert item.metadata_json["last_error"]["code"] == "MEDIA_TOO_LONG"
    assert fakes.adapter.download_calls == 0
    assert fakes.provider.calls == []


def test_duplicate_dispatch_skips(db_session, fakes, prepared) -> None:
    item, _first = prepared()
    assert item.status == "transcribed"
    before = dict(fakes.storage.objects)
    result = preparation.prepare_source_item(
        item.id, db_session, fakes.adapter, fakes.storage, fakes.provider
    )
    assert result == {"item_id": item.id, "skipped": "transcribed"}
    assert fakes.storage.objects == before


def test_failure_records_error_code(db_session, fakes, prepared) -> None:
    fakes.provider.error = TranscriptionError("model load failed")
    item, _result = prepared()
    assert item.status == "failed"
    assert item.metadata_json["last_error"]["code"] == "ASR_FAILED"

    # 从 failed 重跑可恢复成功（failed→resolved 是状态机白名单路径）
    fakes.provider.error = None
    result2 = preparation.prepare_source_item(
        item.id, db_session, fakes.adapter, fakes.storage, fakes.provider
    )
    assert result2["status"] == "transcribed"
    db_session.expire_all()
    assert db_session.get(SourceItem, item.id).status == "transcribed"


def test_enrich_backfills_only_null(db_session, fakes, prepared) -> None:
    item, _result = prepared()
    db_session.expire_all()
    fresh = db_session.get(SourceItem, item.id)
    assert fresh.title == "旧标题"  # 已有值不被覆盖
    assert fresh.published_at == datetime(2026, 3, 15, tzinfo=UTC)  # 空值被回填
    assert fresh.duration_ms == 1250500


def test_intermediate_status_persisted(db_session, fakes, prepared) -> None:
    # ENG-1A 前提：provider 运行时，media_ready→transcribing 已落库（commit 释放行锁）
    _item, _result = prepared()
    assert fakes.provider.probed_status == "transcribing"
