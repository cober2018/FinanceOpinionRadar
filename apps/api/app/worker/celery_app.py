from celery import Celery

from app.core.settings import get_settings

# include= 让 worker 进程加载任务模块；缺了它任务注册表为空，beat 派发的任务会被拒收
celery_app = Celery(
    "radar",
    broker=get_settings().redis_url,
    backend=get_settings().redis_url,
    include=["app.worker.tasks"],
)
celery_app.conf.task_default_queue = "default"

# C6：到期账号派发调度；间隔可配（DISCOVER_DISPATCH_INTERVAL_SEC，默认 300s）
# 注记②：discovered 存量周期补扫（PREPARE_SWEEP_INTERVAL_SEC，默认 600s）——
# 任务投递丢失/进程崩溃后的兜底路径
# RAD-101 队列拆分：重活（媒体/ASR/LLM）与普通任务分流，避免互相堵塞。
# dev 单 worker 用 make worker-beat（-Q 全消费）；prod compose 按队列分进程。
celery_app.conf.task_routes = {
    "prepare_source_item": {"queue": "media"},
    "prepare_live_segment": {"queue": "media"},
    "extract_source_item_viewpoints": {"queue": "llm"},
    "discover_source_account": {"queue": "media"},
}

celery_app.conf.beat_schedule = {
    "dispatch-due-discoveries": {
        "task": "dispatch_due_discoveries",
        "schedule": get_settings().discover_dispatch_interval_sec,
    },
    "dispatch-pending-prepares": {
        "task": "dispatch_pending_prepares",
        "schedule": get_settings().prepare_sweep_interval_sec,
    },
    # Plan #4 Task 5/6：直播分片扫描 + 值守桥同步（服务端未配置时任务内 no-op）
    "ingest-live-segments": {
        "task": "ingest_live_segments",
        "schedule": get_settings().live_scan_interval_sec,
    },
    "sync-live-monitors": {
        "task": "sync_live_monitors",
        "schedule": get_settings().recorder_sync_interval_sec,
    },
    # EPIC-04：观点抽取补扫（transcribed → extracting）
    "dispatch-pending-extractions": {
        "task": "dispatch_pending_extractions",
        "schedule": get_settings().extraction_sweep_interval_sec,
    },
}
