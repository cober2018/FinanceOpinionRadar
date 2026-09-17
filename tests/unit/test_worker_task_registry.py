# Regression: ISSUE-001 — celery_app 未 include 任务模块，worker 进程任务注册表为空，
# beat 派发的 dispatch_due_discoveries 被拒收（Received unregistered task）。
# 单测直调 task.run() 绕过注册表，未覆盖此路径。
# Found by /qa on 2026-09-17
# Report: .gstack/qa-reports/qa-report-localhost-2026-09-17.md
from app.worker.celery_app import celery_app


def test_worker_registry_contains_epic02_tasks() -> None:
    # include= 的模块在 worker 启动时才加载；测试里显式触发同一加载路径
    celery_app.loader.import_default_modules()
    assert "dispatch_due_discoveries" in celery_app.tasks
    assert "discover_source_account" in celery_app.tasks


def test_worker_registry_contains_prepare_tasks() -> None:
    # 注记②：prepare 任务 + discovered 周期补扫必须在 worker 注册表里
    celery_app.loader.import_default_modules()
    assert "prepare_source_item" in celery_app.tasks
    assert "dispatch_pending_prepares" in celery_app.tasks


def test_beat_schedule_wires_prepare_sweep() -> None:
    sched = celery_app.conf.beat_schedule.get("dispatch-pending-prepares")
    assert sched is not None and sched["task"] == "dispatch_pending_prepares"
    assert sched["schedule"] > 0
