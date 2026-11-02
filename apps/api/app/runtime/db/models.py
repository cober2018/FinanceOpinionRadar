"""全部 ORM 模型的聚合注册点（alembic metadata 与 mapper configure 依赖）。"""

from app.modules.content.models import (  # noqa: F401
    DeletedItemRef,
    LiveChatMessage,
    SourceItem,
)
from app.modules.content.models_media import (  # noqa: F401
    MediaAsset,
    TranscriptSegment,
)
from app.modules.delivery.models import (  # noqa: F401
    ApiCallLog,
    ApiKey,
    AuditLog,
    PushChannel,
    PushDelivery,
    VideoSummaryDelivery,
    VideoSummaryEvent,
)
from app.modules.insights.models import (  # noqa: F401
    ContentSummary,
    CreatorTopicSnapshot,
    Entity,
    EntityCandidate,
    PromptVersion,
    Topic,
    TopicConsensusDaily,
    Viewpoint,
    ViewpointEvidence,
)
from app.modules.sources.models import Creator, SourceAccount  # noqa: F401
from app.runtime.models import AppSetting, JobRun  # noqa: F401
