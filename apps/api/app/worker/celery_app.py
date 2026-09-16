from celery import Celery

from app.core.settings import get_settings

celery_app = Celery("radar", broker=get_settings().redis_url, backend=get_settings().redis_url)
celery_app.conf.task_default_queue = "default"

# C6：到期账号派发调度；间隔可配（DISCOVER_DISPATCH_INTERVAL_SEC，默认 300s）
celery_app.conf.beat_schedule = {
    "dispatch-due-discoveries": {
        "task": "dispatch_due_discoveries",
        "schedule": get_settings().discover_dispatch_interval_sec,
    }
}
