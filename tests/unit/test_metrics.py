"""/metrics 端点与运行指标模块（runtime/metrics.py）的单元测试。

Redis/DB 不依赖：跨进程与采集路径全部 best-effort，测试只验证进程内行为与
文本格式契约（Prometheus 0.0.4：每族一组 HELP/TYPE、标签转义、数值格式）。
"""

from fastapi.testclient import TestClient


def _client() -> TestClient:
    from app.bootstrap.asgi import app

    return TestClient(app)


def test_health_and_metrics_endpoint_shape():
    client = _client()
    # 先打两个真实请求产生计数
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "# TYPE radar_http_requests_total counter" in body
    assert 'route="/api/v1/health",status="200"' in body
    # /metrics 自身不计入（避免抓取噪声）
    assert 'route="/metrics"' not in body
    # 耗时累计与身份池 1h 窗口 gauge 必须在场（窗口值可为 0）
    assert "# TYPE radar_http_request_seconds_total counter" in body
    assert "radar_identity_pool_exhaustion_1h" in body


def test_label_escaping_and_ordering():
    from app.runtime.metrics import _fmt_labels

    assert _fmt_labels({"b": "1", "a": "x"}) == '{a="x",b="1"}'  # 键序固定
    assert _fmt_labels({}) == ""
    assert _fmt_labels({"k": 'q"v\\n'}) == '{k="q\\"v\\\\n"}'


def test_counter_accumulates():
    from app.runtime import metrics

    metrics.inc_http("radar_http_requests_total", "200", "/t/one")
    metrics.inc_http("radar_http_requests_total", "200", "/t/one")
    body = metrics.render()
    line = [l for l in body.splitlines() if l.startswith('radar_http_requests_total{route="/t/one"')]
    assert line and line[0].endswith(" 2")


def test_celery_signal_handlers_are_safe_noop():
    """信号处理器对残缺参数必须静默（指标绝不影响任务）。"""
    from app.bootstrap import celery as celery_mod
    from app.runtime import metrics

    celery_mod._metrics_task_prerun(task=None)  # type: ignore[arg-type]
    celery_mod._metrics_task_postrun(task=None, state=None)  # type: ignore[arg-type]

    class _FakeReq:
        id = "r1"

    class _FakeTask:
        name = "unit.fake"
        request = _FakeReq()

    celery_mod._metrics_task_prerun(task=_FakeTask())
    celery_mod._metrics_task_postrun(task=_FakeTask(), state="SUCCESS")
    # Redis 不可用时静默：render 仍成功（共享计数缺席不报错）
    assert "radar_http_requests_total" in metrics.render()
