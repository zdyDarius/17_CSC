"""只读订单明细适配器。

背景：edu-data 公开 API 的订单列表/详情不返回 orderItemId 和报名课程明细，
但退款、工单接口都要求 orderItemId（项目计划第二部分第 3 条）。

边界与约束：
- 本适配器只补偿该缺口，仅执行白名单表的 SELECT（order_item、refund_request）。
- 所有业务写操作仍只能调用 edu-data API，禁止 INSERT/UPDATE/DELETE。
- 独立配置（EDU_READONLY_DB_URL），建议最小权限只读账号。
- edu-data 后续若补齐接口，本适配器可整体替换为 API 实现，不影响主流程。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from edu_assistant.conf.config import get_settings
from edu_assistant.utils.errors import EduDataApiError
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

# 语句白名单：只允许这两条 SELECT
_SELECT_ORDER_ITEMS = text(
    """
    SELECT id, order_id, user_id, student_id, cohort_id, order_item_status,
           item_name, unit_price, discount_amount, payable_amount,
           service_period_days, created_at
    FROM order_item
    WHERE order_id = :order_id AND user_id = :user_id
    ORDER BY id ASC
    """
)

_SELECT_REFUNDS_FOR_ORDER = text(
    """
    SELECT id, refund_no, order_id, order_item_id, payment_id, user_id,
           refund_type, refund_reason, refund_status, apply_amount,
           approved_amount, applied_at
    FROM refund_request
    WHERE order_id = :order_id AND user_id = :user_id
    ORDER BY id DESC
    """
)


class OrderItemAdapter:
    """限定表的只读明细查询。"""

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None

    async def start(self) -> None:
        if self._engine is None:
            self._engine = create_async_engine(
                get_settings().edu_readonly_db_url,
                pool_pre_ping=True,
                pool_recycle=3600,
                pool_size=2,
                max_overflow=2,
            )

    async def stop(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    def _get_engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("只读适配器未初始化，请通过 lifespan 启动")
        return self._engine

    async def list_order_items(self, user_id: int, order_id: int) -> list[dict[str, Any]]:
        """查询订单下全部明细（含状态/名称/金额）。"""
        try:
            engine = self._get_engine()
            async with engine.connect() as conn:
                rows = (
                    await conn.execute(
                        _SELECT_ORDER_ITEMS, {"order_id": order_id, "user_id": user_id}
                    )
                ).mappings()
                return [dict(row) for row in rows]
        except RuntimeError:
            raise
        except Exception as exc:
            logger.error("只读明细查询失败 order_id=%s: %s", order_id, exc)
            raise EduDataApiError("ORDER_ITEM_QUERY_FAILED", "订单明细暂时无法查询", 503) from exc

    async def list_refunds_for_order(self, user_id: int, order_id: int) -> list[dict[str, Any]]:
        """查询订单下全部退款申请（含 order_item_id 映射，供退款类工单关联）。"""
        try:
            engine = self._get_engine()
            async with engine.connect() as conn:
                rows = (
                    await conn.execute(
                        _SELECT_REFUNDS_FOR_ORDER, {"order_id": order_id, "user_id": user_id}
                    )
                ).mappings()
                return [dict(row) for row in rows]
        except RuntimeError:
            raise
        except Exception as exc:
            logger.error("只读退款查询失败 order_id=%s: %s", order_id, exc)
            raise EduDataApiError("REFUND_QUERY_FAILED", "退款申请暂时无法查询", 503) from exc


# —— 展示格式化（供 Action 复用） ——

ORDER_ITEM_STATUS_LABELS = {
    "pending": "待支付",
    "paid": "已支付",
    "completed": "已完成",
    "cancelled": "已取消",
    "refunded": "已退款",
}

REFUND_STATUS_LABELS = {
    "pending": "待审批",
    "approved": "已通过",
    "rejected": "已驳回",
    "refunded": "已退款",
}


def format_order_items(items: list[dict[str, Any]]) -> str:
    """把明细列表渲染为可展示文本（多明细时供用户选择）。"""
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        status = ORDER_ITEM_STATUS_LABELS.get(
            str(item.get("order_item_status")), str(item.get("order_item_status"))
        )
        lines.append(
            f"{index}. {item.get('item_name')}（{status}，"
            f"实付 ¥{item.get('payable_amount')}）"
        )
    return "\n".join(lines)
