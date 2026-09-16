from celery import Celery

from app.core.settings import get_settings

celery_app = Celery("radar", broker=get_settings().redis_url, backend=get_settings().redis_url)
celery_app.conf.task_default_queue = "default"
