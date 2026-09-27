"""Opt-in Webhook delivery for immutable video-summary events."""

import hashlib
import hmac
import ipaddress
import json
import socket
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.db.models import PushChannel, VideoSummaryDelivery, VideoSummaryEvent
from app.services.video_summary_feed import serialize_event


def validate_target(url: str, *, resolve: bool = False) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("视频总结 Webhook 地址格式无效") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise ValueError("视频总结 Webhook 需要无凭据、标准端口的 HTTPS 公网地址")
    host = parsed.hostname
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise ValueError("视频总结 Webhook 不允许私网或本机地址")
    except ValueError as exc:
        if "不允许" in str(exc):
            raise
        if host in ("localhost",) or host.endswith(".localhost") or "." not in host:
            raise ValueError("视频总结 Webhook 需要公网域名") from exc
    if resolve:
        _resolved_public_ip(host)
    return url


def _resolved_public_ip(host: str) -> str:
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("视频总结 Webhook 域名无法解析") from exc
    if not addresses or any(
        not ipaddress.ip_address(result[4][0]).is_global for result in addresses
    ):
        raise ValueError("视频总结 Webhook 域名解析到非公网地址")
    return addresses[0][4][0]


def _post(channel: PushChannel, event: VideoSummaryEvent, http: httpx.Client) -> int:
    cfg = channel.config_json or {}
    url = validate_target(str(cfg.get("url") or ""))
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    address = _resolved_public_ip(host)
    netloc = f"[{address}]" if ":" in address else address
    pinned_url = urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))
    secret = str(cfg.get("secret") or "")
    if not secret:
        raise ValueError("视频总结 Webhook 缺少签名密钥")
    payload = serialize_event(event)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    response = http.post(
        pinned_url,
        content=body,
        headers={
            "Content-Type": "application/json",
            "Host": host,
            "X-Radar-Event-ID": payload["event_id"],
            "X-Radar-Timestamp": timestamp,
            "X-Radar-Signature": f"sha256={signature}",
        },
        timeout=get_settings().open_push_timeout_sec,
        follow_redirects=False,
        extensions={"sni_hostname": host},
    )
    return response.status_code


def deliver_pending_summary_events(session: Session, http: httpx.Client | None = None) -> dict:
    """Run from beat; one event ID remains stable across every attempt."""
    counters = {"sent": 0, "failed": 0, "dead": 0, "unknown": 0}
    channels = session.scalars(
        select(PushChannel).where(
            PushChannel.enabled.is_(True), PushChannel.summary_enabled.is_(True)
        )
    ).all()
    client = http or httpx.Client(
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0),
    )
    try:
        for channel in channels:
            start = int((channel.config_json or {}).get("summary_from_event_id") or 0)
            terminal = (
                select(VideoSummaryDelivery.id)
                .where(
                    VideoSummaryDelivery.channel_id == channel.id,
                    VideoSummaryDelivery.event_id == VideoSummaryEvent.id,
                    VideoSummaryDelivery.status.in_(("sent", "dead", "unknown")),
                )
                .exists()
            )
            events = session.scalars(
                select(VideoSummaryEvent)
                .where(VideoSummaryEvent.id > start, ~terminal)
                .order_by(VideoSummaryEvent.id)
                .limit(200)
            ).all()
            for event in events:
                # A row lock prevents overlapping beat runs from posting the same event twice.
                locked_channel = session.scalar(
                    select(PushChannel).where(PushChannel.id == channel.id).with_for_update()
                )
                if (
                    locked_channel is None
                    or not locked_channel.enabled
                    or not locked_channel.summary_enabled
                ):
                    session.rollback()
                    break
                delivery = session.scalars(
                    select(VideoSummaryDelivery).where(
                        VideoSummaryDelivery.channel_id == channel.id,
                        VideoSummaryDelivery.event_id == event.id,
                    )
                ).first()
                if delivery is not None and delivery.status in ("sent", "dead", "unknown"):
                    session.commit()
                    continue
                if delivery is None:
                    delivery = VideoSummaryDelivery(channel_id=channel.id, event_id=event.id)
                    session.add(delivery)
                delivery.attempt = (delivery.attempt or 0) + 1
                try:
                    status = _post(channel, event, client)
                    delivery.last_http_status = status
                    if 200 <= status < 300:
                        delivery.status = "sent"
                        delivery.error = None
                        counters["sent"] += 1
                    else:
                        delivery.status = "failed"
                        delivery.error = f"HTTP {status}"
                        counters["failed"] += 1
                except httpx.RequestError as exc:
                    delivery.status = "unknown"
                    delivery.error = type(exc).__name__
                    counters["unknown"] += 1
                except ValueError as exc:
                    delivery.status = "dead"
                    delivery.error = str(exc)[:300]
                    counters["dead"] += 1
                if (
                    delivery.status == "failed"
                    and delivery.attempt >= get_settings().open_push_max_attempts
                ):
                    delivery.status = "dead"
                    counters["dead"] += 1
                session.commit()
        return counters
    finally:
        if http is None:
            client.close()
