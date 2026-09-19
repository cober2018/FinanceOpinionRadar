"""dtk（Evil0ctal Douyin_TikTok_Download_API 5.x）HTTP 客户端。

只做 HTTP + 异常映射，不做字段映射（C2 分层）。dtk 返回归一化 schema（Task 1
决策记录），信封为 {"success": bool, "data" | "error"}；`wait=30` 同步直返，
30s 内未完成的任务返回 202（映射为超时语义，由上层按 prepare 失败处置）。
"""

import httpx

from app.services.media.contracts import AdapterProcessError, AdapterTimeoutError

# dtk wait 参数上限 30s；超限 400
_WAIT_SEC = 30
# 非 2xx 时截断进错误消息的 body 长度（DX R3：进 last_error 供排障与 cookie 过期判别）
_BODY_SNIPPET_CHARS = 200
# dtk API 面的闸键：dtk 容器出口恒为本机 IP，与 CDN 下载出口分开计数
_DTK_GATE_EGRESS = "dtk"


class DouyinApiClient:
    """dtk REST 客户端：连接复用，方法体只做取数与异常翻译。"""

    def __init__(self, base_url: str, api_key: str, timeout_sec: int = 60) -> None:
        if not base_url:
            raise AdapterProcessError("DouyinApiClient 需要 base_url（DOUYIN_API_BASE_URL）")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=timeout_sec,
        )

    def fetch_one_video(self, aweme_id: str) -> dict:
        return self._fetch("/api/v1/douyin/video", {"aweme_id": aweme_id})

    def fetch_user_posts(self, sec_uid: str, max_cursor: int = 0, count: int = 20) -> dict:
        return self._fetch(
            "/api/v1/douyin/user/posts",
            {"sec_user_id": sec_uid, "cursor": max_cursor, "count": count},
        )

    def _fetch(self, path: str, params: dict) -> dict:
        """出口并发闸内执行：dtk 上游 429 的防护面（egress_max_concurrency=0 关闸）。"""
        from app.services.download_gate import egress_limit, get_download_gate

        limit = egress_limit()
        if limit <= 0:
            return self._do_fetch(path, params)
        from app.core.settings import get_settings

        with get_download_gate().slot(
            _DTK_GATE_EGRESS,
            limit=limit,
            ttl_sec=get_settings().egress_slot_ttl_sec,
        ):
            return self._do_fetch(path, params)

    def _do_fetch(self, path: str, params: dict) -> dict:
        try:
            resp = self._client.get(path, params={**params, "wait": _WAIT_SEC})
        except httpx.TimeoutException as exc:
            raise AdapterTimeoutError(
                f"dtk 请求超时（>{self._client.timeout}）: {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AdapterProcessError(f"dtk 请求失败: {exc}") from exc
        if resp.status_code == 202:  # 任务在 wait 窗口内未完成
            raise AdapterTimeoutError(f"dtk 解析任务超 { _WAIT_SEC}s 未完成: {path}")
        if resp.status_code >= 400:
            raise AdapterProcessError(
                f"dtk 上游 HTTP {resp.status_code}: {resp.text[:_BODY_SNIPPET_CHARS]!r}"
            )
        body = resp.json()
        if not body.get("success"):
            err = body.get("error") or {}
            raise AdapterProcessError(
                f"dtk 上游错误 [{err.get('code', 'UNKNOWN')}]: {err.get('message', '')}"
            )
        return body.get("data") or {}
