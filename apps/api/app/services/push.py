"""确认观点推送（Plan #7）：新确认观点 → 飞书/钉钉/通用 webhook。

派发模型（不侵入确认代码路径，天然幂等）：
- 每轮对每个 enabled 渠道找「confirmed 且该渠道无 sent/dead 投递」的观点；
- feishu/dingtalk 聚合一条 digest；generic_webhook 一次 POST JSON 批量事件；
- 失败 attempt+1，达到上限置 dead（告警日志）不再重试；
- 漏发自愈：任何未 sent 的 confirmed 观点下一轮仍会被扫到。
"""

import base64
import hashlib
import hmac
import time
from urllib.parse import quote_plus

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import Creator, Entity, PushChannel, PushDelivery, Viewpoint

logger = structlog.get_logger(__name__)

_CHANNEL_TYPES = ("generic_webhook", "feishu", "dingtalk")
_BATCH_LIMIT = 20  # 单渠道单轮最多携带的观点数（digest 防超长）

_STANCE_CN = {
    "strong_bullish": "强烈看多",
    "bullish": "看多",
    "neutral": "中性",
    "bearish": "看空",
    "strong_bearish": "强烈看空",
    "unclear": "方向不明",
}


def _sign(secret: str, timestamp: int) -> str:
    """飞书/钉钉机器人同款加签：HMAC-SHA256(key=secret, msg="ts\\nsecret") → base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _with_dingtalk_sign(url: str, secret: str) -> str:
    ts = int(time.time())
    sign = quote_plus(_sign(secret, ts))
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timestamp={ts}&sign={sign}"


def _serialize_vp_row(vp: Viewpoint, creator_name: str | None, entity_name: str | None) -> dict:
    return {
        "viewpoint_id": vp.id,
        "creator_name": creator_name,
        "claim": vp.claim,
        "stance": vp.stance,
        "horizon": vp.horizon,
        "entity_name": entity_name,
        "entity_raw": vp.entity_raw,
        "confidence": float(vp.confidence or 0.5),
        "as_of_date": vp.as_of_date.isoformat() if vp.as_of_date else None,
        "confirmed_via_updated_at": vp.updated_at.isoformat() if vp.updated_at else None,
    }


def _digest_text(rows: list[tuple[Viewpoint, str | None, str | None]]) -> str:
    lines = [f"【观点雷达】新增确认观点 {len(rows)} 条", ""]
    for i, (vp, creator_name, entity_name) in enumerate(rows, 1):
        entity = entity_name or vp.entity_raw or "未指明标的"
        stance = _STANCE_CN.get(vp.stance, vp.stance)
        claim = vp.claim[:60] + ("…" if len(vp.claim) > 60 else "")
        lines.append(
            f"{i}. {creator_name or '未知主播'} · {entity} · {stance}"
            f"（置信 {float(vp.confidence or 0.5):.2f}）"
        )
        lines.append(f"   {claim}")
    return "\n".join(lines)


def _build_payload(
    channel: PushChannel, rows: list[tuple[Viewpoint, str | None, str | None]], *, test: bool = False
) -> tuple[str, dict | None]:
    """返回 (url, json_body)；text 类消息体见 _build_text。"""
    cfg = channel.config_json or {}
    url = cfg.get("url") or ""
    secret = cfg.get("secret") or None
    if channel.channel_type == "dingtalk":
        if secret:
            url = _with_dingtalk_sign(url, secret)
        text = "观点雷达推送测试：连通性 OK" if test else _digest_text(rows)
        return url, {"msgtype": "markdown", "markdown": {"title": "观点雷达", "text": text}}
    if channel.channel_type == "feishu":
        text = "观点雷达推送测试：连通性 OK" if test else _digest_text(rows)
        body: dict = {"msg_type": "text", "content": {"text": text}}
        if secret:
            ts = int(time.time())
            body["timestamp"] = str(ts)
            body["sign"] = _sign(secret, ts)
        return url, body
    # generic_webhook
    items = [_serialize_vp_row(vp, c, e) for vp, c, e in rows]
    event = "test" if test else "viewpoints.confirmed"
    return url, {"event": event, "items": items}


def _send(channel: PushChannel, url: str, body: dict | None, http: httpx.Client) -> None:
    """非 2xx / 网络异常统一抛 RuntimeError；钉钉/飞书业务错误码也视为失败。"""
    if not url:
        raise RuntimeError("渠道未配置 url")
    resp = http.post(url, json=body, timeout=get_settings().open_push_timeout_sec)
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
    if channel.channel_type == "feishu":
        code = (resp.json() or {}).get("code")
        if code not in (None, 0):
            raise RuntimeError(f"飞书错误码 {code}: {resp.text[:120]}")
    if channel.channel_type == "dingtalk":
        code = (resp.json() or {}).get("errcode")
        if code not in (None, 0):
            raise RuntimeError(f"钉钉错误码 {code}: {resp.text[:120]}")


def send_test_message(session: Session, channel_id: int, http: httpx.Client | None = None) -> dict:
    """管理台「测试」按钮：立即发一条测试消息验证渠道配置。"""
    channel = session.get(PushChannel, channel_id)
    if channel is None:
        raise LookupError(f"push_channel {channel_id} 不存在")
    client = http or httpx.Client()
    try:
        url, body = _build_payload(channel, [], test=True)
        _send(channel, url, body, client)
        return {"channel_id": channel_id, "ok": True}
    finally:
        if http is None:
            client.close()


def deliver_pending_pushes(session: Session, http: httpx.Client | None = None) -> dict:
    """beat 入口：扫描未投递的确认观点并按渠道推送。"""
    s = get_settings()
    counters = {"channels": 0, "delivered": 0, "failed": 0, "dead": 0}
    channels = session.scalars(
        select(PushChannel).where(PushChannel.enabled.is_(True))
    ).all()
    client = http or httpx.Client()
    try:
        for channel in channels:
            rows = (
                session.execute(
                    select(Viewpoint, Creator.display_name, Entity.canonical_name)
                    .join(Creator, Creator.id == Viewpoint.creator_id)
                    .outerjoin(Entity, Entity.id == Viewpoint.entity_id)
                    .where(
                        Viewpoint.verification_status == "confirmed",
                        ~select(PushDelivery.id)
                        .where(
                            PushDelivery.channel_id == channel.id,
                            PushDelivery.viewpoint_id == Viewpoint.id,
                            PushDelivery.status.in_(("sent", "dead")),
                        )
                        .exists(),
                    )
                    .order_by(Viewpoint.updated_at.desc())
                    .limit(_BATCH_LIMIT)
                )
                .all()
            )
            if not rows:
                continue
            counters["channels"] += 1
            deliveries = {
                d.viewpoint_id: d
                for d in session.scalars(
                    select(PushDelivery).where(
                        PushDelivery.channel_id == channel.id,
                        PushDelivery.viewpoint_id.in_([vp.id for vp, _, _ in rows]),
                    )
                )
            }
            try:
                url, body = _build_payload(channel, rows)
                _send(channel, url, body, client)
            except Exception as exc:  # noqa: BLE001 网络失败记账进 delivery，不中断其他渠道
                for vp, _, _ in rows:
                    d = deliveries.get(vp.id) or PushDelivery(
                        channel_id=channel.id, viewpoint_id=vp.id, attempt=0
                    )
                    if d.id is None:
                        session.add(d)
                    d.attempt = (d.attempt or 0) + 1
                    d.status = "failed"
                    d.error = str(exc)[:500]
                    if d.attempt >= s.open_push_max_attempts:
                        d.status = "dead"
                        counters["dead"] += 1
                    counters["failed"] += 1
                session.commit()  # 失败/dead 也必须落库，否则下轮看不到 attempt 计数
                logger.warning(
                    "push_channel_failed",
                    channel=channel.name,
                    error=str(exc)[:150],
                    viewpoints=len(rows),
                )
                continue
            # 投递时间以 updated_at 为准（TimestampMixin onupdate）
            for vp, _, _ in rows:
                d = deliveries.get(vp.id) or PushDelivery(
                    channel_id=channel.id, viewpoint_id=vp.id
                )
                if d.id is None:
                    session.add(d)
                d.status = "sent"
                d.error = None
                counters["delivered"] += 1
            session.commit()
        if counters["channels"]:
            logger.info("push_deliver_done", **counters)
        return counters
    finally:
        if http is None:
            client.close()
