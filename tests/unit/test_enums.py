"""枚举值集 = PRD-derived 契约（§4.3/§4.4/§11/§7.9）；抽取 spike（G1 tracer bullet）后复评。"""

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
        "strong_bullish",
        "bullish",
        "neutral",
        "bearish",
        "strong_bearish",
        "unclear",
    }


def test_change_type_matches_prd() -> None:
    assert {c.value for c in ChangeType} == {
        "new_thesis",
        "strengthening",
        "weakening",
        "stance_flip",
        "horizon_change",
        "repeated",
        "expired",
        "unclear",
    }


def test_horizon_matches_prd() -> None:
    assert {h.value for h in Horizon} == {"intraday", "1-3D", "1-4W", "1-3M", "3M+"}


def test_source_item_status_value_set() -> None:
    assert {s.value for s in SourceItemStatus} == {
        "discovered",
        "resolved",
        "media_ready",
        "transcribing",
        "transcribed",
        "extracting",
        "reviewing",
        "ready",
        "failed",
        "ignored",
    }


def test_item_type_values() -> None:
    assert {i.value for i in ItemType} == {"vod", "live"}


def test_discovery_mode_values() -> None:
    assert {d.value for d in DiscoveryMode} == {"manual", "auto_poll"}


def test_job_types_match_prd_p09() -> None:
    assert {j.value for j in JobType} == {
        "DISCOVER",
        "RESOLVE_MEDIA",
        "DOWNLOAD_MEDIA",
        "FETCH_SUBTITLE",
        "TRANSCRIBE",
        "ALIGN",
        "CHUNK",
        "EXTRACT_VIEWPOINT",
        "NORMALIZE_ENTITY",
        "REVIEW_VIEWPOINT",
        "BUILD_SNAPSHOT",
        "BUILD_CONSENSUS",
    }


def test_job_status_values() -> None:
    assert {j.value for j in JobStatus} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_verification_status_values() -> None:
    assert {v.value for v in VerificationStatus} == {
        "candidate",
        "auto_verified",
        "review_required",
        "reviewed",
        "rejected",
    }
