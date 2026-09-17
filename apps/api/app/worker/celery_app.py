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
celery_app.conf.beat_schedule = {
    "dispatch-due-discoveries": {
        "task": "dispatch_due_discoveries",
        "schedule": get_settings().discover_dispatch_interval_sec,
    },
    "dispatch-pending-prepares": {
        "task": "dispatch_pending_prepares",
        "schedule": get_settings().prepare_sweep_interval_sec,
    },
}
