"""环境代理免疫（2026-09-27 实录）：no_proxy 含 IPv6 CIDR（::1/128）时裸 httpx.Client()
构造即崩（httpx 对 no_proxy 条目生成非法 mount URL），全 LLM/下载/推送管线瘫痪。

本应用的所有出站客户端一律 trust_env=False（出口代理由应用显式管理：qg 池/直连），
环境变量再毒也不能影响业务。此文件固化该约束。
"""

import httpx
import pytest

POISON_NO_PROXY = "127.0.0.1,localhost,::1,127.0.0.0/8,::1/128"


def test_vanilla_client_crashes_on_poisoned_no_proxy(monkeypatch):
    """对照：证明毒环境真实存在（httpx 上游 bug，trust_env=True 默认路径）。"""
    monkeypatch.setenv("no_proxy", POISON_NO_PROXY)
    with pytest.raises(httpx.InvalidURL):
        httpx.Client()


def test_douyin_clients_construct_under_poisoned_env(monkeypatch):
    from unittest.mock import Mock

    from app.services.media.adapters.douyin import DouyinAdapter
    from app.services.media.adapters.douyin_client import DouyinApiClient

    monkeypatch.setenv("no_proxy", POISON_NO_PROXY)
    DouyinApiClient(base_url="http://127.0.0.1:8080", api_key="k")  # 构造期建客户端（曾在此崩溃）
    adapter = DouyinAdapter(  # 不传 http → 走默认构造（曾在此崩溃）
        Mock(),
        allowlist=("douyin.com",),
        cdn_allowlist=("douyinvod.com",),
        discover_max_pages=1,
    )
    adapter._http.close()


def test_qg_and_llm_clients_construct_under_poisoned_env(monkeypatch):
    from app.services.qg_proxy import QgLongtermClient

    monkeypatch.setenv("no_proxy", POISON_NO_PROXY)
    c = QgLongtermClient("key", "pwd")
    c._client.close()
    # LLM provider 构造（_post 才发请求，构造期建客户端的路径覆盖 push/douyin 已足）
    from app.llm.provider import OpenAICompatProvider

    provider = OpenAICompatProvider(
        base_url="http://127.0.0.1:9/v1", api_key="k", model="m"
    )
    assert provider._base == "http://127.0.0.1:9/v1"
