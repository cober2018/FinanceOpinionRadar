"""URL 安全闸：Adapter 派生子进程前的第一道校验（RAD-021）。"""

from urllib.parse import urlsplit

from app.services.media.contracts import UrlNotAllowedError

_ALLOWED_SCHEMES = ("http", "https")


def ensure_allowed_url(raw_url: str, allowlist: tuple[str, ...]) -> None:
    """校验 scheme∈{http,https}、主机命中白名单（自身或子域）、无 userinfo。

    不合法即抛 UrlNotAllowedError——在派生子进程之前拒绝，杜绝
    file://、内网地址、凭据注入等进入 yt-dlp 参数。
    """
    try:
        parts = urlsplit(raw_url)
    except ValueError as exc:
        raise UrlNotAllowedError(f"URL 无法解析: {raw_url!r}") from exc
    # 尾点归一：DNS 里 "youtube.com." 等价 "youtube.com"，是常见绕过手法
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in _ALLOWED_SCHEMES or not host:
        raise UrlNotAllowedError(
            f"URL 协议非法或主机为空: {raw_url!r} (允许: {_ALLOWED_SCHEMES})"
        )
    if parts.username is not None or parts.password is not None:
        raise UrlNotAllowedError(f"URL 不允许携带 userinfo: {raw_url!r}")
    if not any(host == d or host.endswith(f".{d}") for d in allowlist):
        raise UrlNotAllowedError(
            f"主机 {host} 不在白名单 {allowlist}，如需放行请配置 MEDIA_HOST_ALLOWLIST"
        )
