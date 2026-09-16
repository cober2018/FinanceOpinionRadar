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
celery_app.conf.beat_schedule = {
    "dispatch-due-discoveries": {
        "task": "dispatch_due_discoveries",
        "schedule": get_settings().discover_dispatch_interval_sec,
    }
}
