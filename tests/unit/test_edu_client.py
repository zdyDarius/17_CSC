"""edu-data client 测试：httpx.MockTransport，不依赖真实服务。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from edu_assistant.integrations.edu_data.client import EduDataClient
from edu_assistant.utils.errors import EduDataApiError
from edu_assistant.utils.httpx_client import _client


class FakeClientHolder:
    """替换共享 httpx client（测试隔离）。"""

    def __init__(self, transport: httpx.MockTransport):
        self.transport = transport
        self.saved = None

    def install(self):
        import edu_assistant.integrations.edu_data.client as mod

        self.saved = mod.get_http_client
        mod.get_http_client = lambda: httpx.AsyncClient(transport=self.transport)

    def restore(self):
        import edu_assistant.integrations.edu_data.client as mod

        if self.saved is not None:
            mod.get_http_client = self.saved


def make_client(handler) -> EduDataClient:
    return EduDataClient(base_url="http://edu.test")


def ok(data):
    return httpx.Response(200, json={"code": 0, "message": "ok", "data": data})


def err(status, code, message):
    return httpx.Response(status, json={"code": code, "message": message, "data": None})


def test_find_order_by_no_matches(tmp_path):
    orders_page1 = {
        "list": [
            {"orderId": 101, "orderNo": "ORD20240401001"},
            {"orderId": 102, "orderNo": "ORD20240401005"},
        ],
        "total": 2,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/orders"
        return ok(orders_page1)

    holder = FakeClientHolder(httpx.MockTransport(handler))
    holder.install()
    try:
        client = make_client(handler)
        row = asyncio.run(client.find_order_by_no("1001", "ORD20240401005"))
        assert row["orderId"] == 102
    finally:
        holder.restore()


def test_find_order_by_no_pagination():
    page1 = {"list": [{"orderId": 1, "orderNo": "A"}], "total": 60}
    page2 = {"list": [{"orderId": 50, "orderNo": "ORD20240401099"}], "total": 60}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return ok(page1 if calls["n"] == 1 else page2)

    holder = FakeClientHolder(httpx.MockTransport(handler))
    holder.install()
    try:
        client = make_client(handler)
        row = asyncio.run(client.find_order_by_no("1001", "ORD20240401099"))
        assert row["orderId"] == 50
    finally:
        holder.restore()


def test_find_order_by_no_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"list": [], "total": 0})

    holder = FakeClientHolder(httpx.MockTransport(handler))
    holder.install()
    try:
        client = make_client(handler)
        row = asyncio.run(client.find_order_by_no("1001", "ORD404"))
        assert row is None
    finally:
        holder.restore()


def test_business_error_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return err(404, "ORDER_NOT_FOUND", "订单不存在")

    holder = FakeClientHolder(httpx.MockTransport(handler))
    holder.install()
    try:
        client = make_client(handler)
        with pytest.raises(EduDataApiError) as exc_info:
            asyncio.run(client.get_order("1001", 9))
        assert exc_info.value.code == "ORDER_NOT_FOUND"
        assert exc_info.value.status_code == 404
    finally:
        holder.restore()


def test_x_user_id_forwarded():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["header"] = request.headers.get("X-User-Id")
        return ok({"studentId": 7})

    holder = FakeClientHolder(httpx.MockTransport(handler))
    holder.install()
    try:
        client = make_client(handler)
        asyncio.run(client.get_student_profile(1001))
        assert seen["header"] == "1001"
    finally:
        holder.restore()
