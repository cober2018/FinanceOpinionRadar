"""领域枚举：数据库存 varchar，应用层用本文件校验（PRD 8.1 额外要求）。

值集为 PRD-derived 契约（§4.3/§4.4/§11/§7.9）；G1 tracer bullet 后复评。
"""

from enum import StrEnum


class Stance(StrEnum):
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"
    UNCLEAR = "unclear"


class Horizon(StrEnum):
    INTRADAY = "intraday"
    ONE_TO_THREE_DAYS = "1-3D"
    ONE_TO_FOUR_WEEKS = "1-4W"
    ONE_TO_THREE_MONTHS = "1-3M"
    THREE_MONTHS_PLUS = "3M+"


class ChangeType(StrEnum):
    NEW_THESIS = "new_thesis"
    STRENGTHENING = "strengthening"
    WEAKENING = "weakening"
    STANCE_FLIP = "stance_flip"
    HORIZON_CHANGE = "horizon_change"
    REPEATED = "repeated"
    EXPIRED = "expired"
    UNCLEAR = "unclear"


class SourceItemStatus(StrEnum):
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


class ItemType(StrEnum):
    VOD = "vod"
    LIVE = "live"


class DiscoveryMode(StrEnum):
    MANUAL = "manual"
    AUTO_POLL = "auto_poll"


class VerificationStatus(StrEnum):
    CANDIDATE = "candidate"
    AUTO_VERIFIED = "auto_verified"
    REVIEW_REQUIRED = "review_required"
    REVIEWED = "reviewed"
    REJECTED = "rejected"


class JobType(StrEnum):
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


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
