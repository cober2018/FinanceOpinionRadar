import pytest
from app.services.media.contracts import UrlNotAllowedError
from app.services.media.url_guard import ensure_allowed_url

ALLOW = ("youtube.com", "youtu.be", "bilibili.com", "douyin.com")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=x",
        "http://youtu.be/x",
        "https://m.bilibili.com/video/BV1xx",
        "https://www.douyin.com/video/1",
    ],
)
def test_allowed_hosts_pass(url: str) -> None:
    ensure_allowed_url(url, ALLOW)  # 不抛即通过


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",  # 协议限制
        "ftp://youtube.com/x",
        "https://evil.com/watch?v=x",  # 主机不在白名单
        "https://youtube.com.evil.com/x",  # 后缀伪造
        "https://user:pass@youtube.com/x",  # userinfo 不可信
        "not-a-url",  # 无 scheme
        "",  # 空串
    ],
)
def test_rejected(url: str) -> None:
    with pytest.raises(UrlNotAllowedError):
        ensure_allowed_url(url, ALLOW)


def test_error_message_names_url_and_allowlist() -> None:
    with pytest.raises(UrlNotAllowedError, match="evil.com"):
        ensure_allowed_url("https://evil.com/x", ALLOW)


def test_trailing_dot_host_is_normalized() -> None:
    # DNS 尾点伪造：youtube.com. 应视作 youtube.com 放行
    ensure_allowed_url("https://youtube.com./watch?v=x", ALLOW)
