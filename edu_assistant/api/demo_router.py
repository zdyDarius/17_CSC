"""演示页上下文接口：聚合当前用户的档案/订单（含明细）/在读班次/退款申请。

只读聚合，供演示页右侧"学籍卡"展示；逐节降级（某节失败不影响其余）。
非演示场景请使用业务 API；本接口不做分页，数据量按演示用户规模（≤10 条）截断。
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from edu_assistant.api.dependencies import get_dialogue_service
from edu_assistant.engine.builder import EngineComponents
from edu_assistant.integrations.edu_data.order_item_adapter import (
    ORDER_ITEM_STATUS_LABELS,
    REFUND_STATUS_LABELS,
)
from edu_assistant.task.action.edu.order_actions import ORDER_STATUS_LABELS
from edu_assistant.task.action.edu.progress_actions import ENROLL_STATUS_LABELS
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

UserIdHeader = Annotated[str | None, Header(alias="X-User-Id")]


def _require_user_id(x_user_id: str | None) -> str:
    if not x_user_id or not x_user_id.strip():
        from edu_assistant.utils.errors import unauthorized

        raise unauthorized("MISSING_USER_ID", "缺少请求头 X-User-Id")
    value = x_user_id.strip()
    if not value.isdigit():
        from edu_assistant.utils.errors import unauthorized

        raise unauthorized("INVALID_USER_ID", "X-User-Id 必须为数字用户 ID")
    return value


async def _load_profile(components: EngineComponents, user_id: str) -> dict | None:
    try:
        raw = await components.edu_client.get_student_profile(user_id)
        return {
            "studentId": raw.get("studentId"),
            "identity": (raw.get("learnerIdentity") or {}).get("name"),
            "goal": (raw.get("learningGoal") or {}).get("name"),
            "education": (raw.get("educationLevel") or {}).get("name"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("演示档案查询失败 user=%s: %s", user_id, exc)
        return None


async def _load_orders_and_refunds(
    components: EngineComponents, user_id: str
) -> tuple[list[dict], list[dict]]:
    """订单列表（含只读明细）+ 退款申请（映射订单号）。逐单降级。"""
    try:
        result = await components.edu_client.list_orders(user_id, page_size=10)
    except Exception as exc:  # noqa: BLE001
        logger.warning("演示订单查询失败 user=%s: %s", user_id, exc)
        return [], []
    rows = (result or {}).get("list", []) or []
    order_id_to_no: dict[int, str] = {}
    orders: list[dict] = []
    for row in rows[:10]:
        order_id = int(row.get("orderId", 0) or 0)
        order_no = str(row.get("orderNo", ""))
        order_id_to_no[order_id] = order_no
        items: list[dict] = []
        if order_id:
            try:
                raw_items = await components.order_item_adapter.list_order_items(
                    int(user_id), order_id
                )
                items = [
                    {
                        "name": str(i.get("item_name", "")),
                        "status": ORDER_ITEM_STATUS_LABELS.get(
                            str(i.get("order_item_status")), str(i.get("order_item_status"))
                        ),
                        "amount": str(i.get("payable_amount", "")),
                    }
                    for i in raw_items[:5]
                ]
            except Exception:  # noqa: BLE001
                items = []
        orders.append(
            {
                "orderNo": order_no,
                "status": ORDER_STATUS_LABELS.get(
                    str(row.get("orderStatusCode", "")), str(row.get("orderStatusCode", ""))
                ),
                "amount": str(row.get("payableAmount", "")),
                "paid": str(row.get("paidAmount")) if row.get("paidAmount") else None,
                "createdAt": str(row.get("createdAt", ""))[:10],
                "items": items,
            }
        )

    refunds: list[dict] = []
    for order_id, order_no in list(order_id_to_no.items())[:10]:
        try:
            rows_refund = await components.order_item_adapter.list_refunds_for_order(
                int(user_id), order_id
            )
        except Exception:  # noqa: BLE001
            continue
        for r in rows_refund[:5]:
            refunds.append(
                {
                    "refundNo": str(r.get("refund_no", "")),
                    "orderNo": order_no,
                    "status": REFUND_STATUS_LABELS.get(
                        str(r.get("refund_status")), str(r.get("refund_status"))
                    ),
                    "amount": str(r.get("apply_amount", "")),
                }
            )
    return orders, refunds


async def _load_cohorts(components: EngineComponents, user_id: str) -> list[dict]:
    try:
        result = await components.edu_client.list_my_cohorts(user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("演示班次查询失败 user=%s: %s", user_id, exc)
        return []
    rows = (result or {}).get("list", []) or []

    async def one(row: dict) -> dict | None:
        try:
            cohort_id = int(row.get("cohortId", 0))
            progress: dict = {}
            try:
                raw = await components.edu_client.get_cohort_progress(user_id, cohort_id)
                progress = {
                    "attendance": f"{(raw.get('attendance') or {}).get('presentCount', 0)}/{(raw.get('attendance') or {}).get('totalSessions', 0)}",
                    "video": f"{(raw.get('video') or {}).get('completedVideos', 0)}/{(raw.get('video') or {}).get('totalVideos', 0)}",
                    "homework": f"{(raw.get('homework') or {}).get('submittedCount', 0)}/{(raw.get('homework') or {}).get('totalHomeworks', 0)}",
                    "exam": f"{(raw.get('exam') or {}).get('submittedCount', 0)}/{(raw.get('exam') or {}).get('totalExams', 0)}",
                }
            except Exception:  # noqa: BLE001
                progress = {}
            return {
                "cohortId": cohort_id,
                "cohortName": str(row.get("cohortName", "")),
                "seriesName": str(row.get("seriesName", "")),
                "status": ENROLL_STATUS_LABELS.get(
                    str(row.get("enrollStatusCode", "")), str(row.get("enrollStatusCode", ""))
                ),
                "progress": progress,
            }
        except Exception:  # noqa: BLE001
            return None

    rows = rows[:6]
    results = await asyncio.gather(*(one(r) for r in rows))
    return [r for r in results if r is not None]


@router.get("/api/demo/context")
async def get_demo_context(
    dialogue_service=Depends(get_dialogue_service),
    x_user_id: UserIdHeader = None,
) -> dict:
    user_id = _require_user_id(x_user_id)
    components: EngineComponents = dialogue_service.components

    profile, orders_refunds, cohorts = await asyncio.gather(
        _load_profile(components, user_id),
        _load_orders_and_refunds(components, user_id),
        _load_cohorts(components, user_id),
    )
    orders, refunds = orders_refunds
    return {
        "userId": user_id,
        "profile": profile,
        "orders": orders,
        "refunds": refunds,
        "cohorts": cohorts,
    }
