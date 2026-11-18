"""共享 httpx.AsyncClient（lifespan 初始化/释放），避免每请求新建连接池。"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def init_http_client() -> None:
    global _client
    if _client is not None:
        return
    from edu_assistant.conf.config import get_settings

    timeout = get_settings().edu_api_timeout_seconds
    _client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def get_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("HTTP client 未初始化，请通过 lifespan 启动")
    return _client
