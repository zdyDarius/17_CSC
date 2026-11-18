"""edu-data API client。

- 统一前缀 /api/v1，身份通过 X-User-Id 转发（服务端绑定，不由 LLM 猜测）。
- GET 网络错误有限重试；4xx/业务冲突不盲目重试。
- POST 不自动重发（写操作防重）。
- 统一解包 {"code":0,"message":"ok","data":...} 与业务错误。
"""

from __future__ import annotations

from typing import Any

import httpx

from edu_assistant.conf.config import get_settings
from edu_assistant.utils.errors import EduDataApiError, EduDataNetworkError
from edu_assistant.utils.httpx_client import get_http_client
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


class EduDataClient:
    """edu-data 业务 API 封装。"""

    def __init__(self, base_url: str | None = None) -> None:
        s = get_settings()
        self.base_url = (base_url or s.edu_api_base_url).rstrip("/")
        self.get_retries = max(0, s.edu_api_get_retries)

    # —— 底层请求 ——

    async def _request(
        self,
        method: str,
        path: str,
        user_id: str | int | None = None,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> Any:
        url = f"{self.base_url}/api/v1{path}"
        headers: dict[str, str] = {}
        if user_id is not None:
            headers["X-User-Id"] = str(user_id)
        client = get_http_client()
        attempts = self.get_retries + 1 if method == "GET" else 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await client.request(
                    method, url, headers=headers, params=params, json=json_body
                )
                break
            except httpx.HTTPError as exc:  # 网络错误/超时
                last_error = exc
                logger.warning(
                    "edu-data 请求失败 attempt=%s/%s %s %s: %s",
                    attempt, attempts, method, path, exc,
                )
                continue
        else:
            raise EduDataNetworkError() from last_error

        if response.status_code >= 500:
            raise EduDataNetworkError("业务服务暂时不可用，请稍后重试")

        try:
            payload = response.json()
        except ValueError:
            raise EduDataApiError(
                "EDU_API_BAD_RESPONSE", f"业务服务响应异常({response.status_code})", 502
            ) from None

        code = payload.get("code")
        if response.status_code >= 400 or (code not in (0, None, "0")):
            raise EduDataApiError(
                str(code or "EDU_API_ERROR"),
                str(payload.get("message") or "业务处理失败"),
                response.status_code,
            )
        return payload.get("data")

    async def get(self, path: str, user_id: str | int | None = None, params: dict | None = None) -> Any:
        return await self._request("GET", path, user_id=user_id, params=params)

    async def post(self, path: str, user_id: str | int | None = None, json_body: dict | None = None) -> Any:
        return await self._request("POST", path, user_id=user_id, json_body=json_body)

    # —— 学员与课程 ——

    async def get_student_profile(self, user_id: str | int) -> dict:
        return await self.get("/me/student-profile", user_id=user_id)

    async def search_series(self, keyword: str) -> dict:
        return await self.get("/series", params={"keyword": keyword, "pageSize": 5})

    async def get_series_detail(self, series_id: int) -> dict:
        return await self.get(f"/series/{series_id}")

    async def list_series_cohorts(self, series_id: int) -> list:
        return await self.get(f"/series/{series_id}/cohorts")

    async def get_cohort_detail(self, cohort_id: int) -> dict:
        return await self.get(f"/cohorts/{cohort_id}")

    # —— 订单 ——

    async def list_orders(
        self, user_id: str | int, page_no: int = 1, page_size: int = 20, status: str | None = None
    ) -> dict:
        params: dict[str, Any] = {"pageNo": page_no, "pageSize": page_size}
        if status:
            params["status"] = status
        return await self.get("/orders", user_id=user_id, params=params)

    async def get_order(self, user_id: str | int, order_id: int) -> dict:
        return await self.get(f"/orders/{order_id}", user_id=user_id)

    async def find_order_by_no(self, user_id: str | int, order_no: str) -> dict | None:
        """订单号 ORD... 是 orderNo；详情接口需要数值 orderId。

        分页遍历订单列表精确匹配 orderNo，再返回列表条目。
        """
        order_no_norm = order_no.strip()
        page_no = 1
        while page_no <= 20:  # 上限保护，避免极端数据无限翻页
            result = await self.list_orders(user_id, page_no=page_no, page_size=50)
            for row in result.get("list", []):
                if str(row.get("orderNo", "")).strip() == order_no_norm:
                    return row
            if page_no * 50 >= int(result.get("total", 0) or 0):
                return None
            page_no += 1
        return None

    # —— 学习 ——

    async def list_my_cohorts(self, user_id: str | int) -> dict:
        return await self.get("/me/cohorts", user_id=user_id, params={"pageSize": 50})

    async def get_cohort_progress(self, user_id: str | int, cohort_id: int) -> dict:
        return await self.get(f"/me/cohorts/{cohort_id}/progress", user_id=user_id)

    async def list_my_exams(self, user_id: str | int, cohort_id: int) -> list:
        return (await self.get(
            "/me/exams", user_id=user_id, params={"cohortId": cohort_id, "pageSize": 50}
        )).get("list", []) or []

    # —— 退款 ——

    async def create_refund_request(
        self,
        user_id: str | int,
        order_item_id: int,
        refund_type: str,
        refund_reason: str,
        apply_amount: str,
        remark: str | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "refundType": refund_type,
            "refundReason": refund_reason,
            "applyAmount": apply_amount,
        }
        if remark:
            body["remark"] = remark
        return await self.post(
            f"/order-items/{order_item_id}/refund-requests",
            user_id=user_id,
            json_body=body,
        )

    async def list_refund_requests(
        self, user_id: str | int, status: str | None = None
    ) -> dict:
        params: dict[str, Any] = {"pageSize": 50}
        if status:
            params["status"] = status
        return await self.get("/refund-requests", user_id=user_id, params=params)

    # —— 工单 ——

    async def create_service_ticket(self, user_id: str | int, body: dict) -> dict:
        return await self.post("/service-tickets", user_id=user_id, json_body=body)

    async def list_service_tickets(self, user_id: str | int) -> dict:
        return await self.get("/service-tickets", user_id=user_id, params={"pageSize": 50})
