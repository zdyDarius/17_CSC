"""教育业务 Action：订单解析与查询。"""

from __future__ import annotations

from edu_assistant.integrations.edu_data.order_item_adapter import (
    ORDER_ITEM_STATUS_LABELS,
    format_order_items,
)
from edu_assistant.task.action.base import ActionContext, ActionResult
from edu_assistant.task.action.registry import register_action
from edu_assistant.utils.errors import EduDataApiError, EduDataNetworkError
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

ORDER_STATUS_LABELS = {
    "pending": "待支付",
    "paid": "已支付",
    "completed": "已完成",
    "cancelled": "已取消",
    "partial_refunded": "部分退款",
    "refunded": "已退款",
}


async def _resolve_order(ctx: ActionContext, order_no: str) -> dict:
    """订单号 → 订单列表条目 + 详情 + 明细（只读适配器）。

    返回 {order, detail, items}；查无/越权时抛 EduDataApiError(ORDER_NOT_FOUND)。
    """
    row = await ctx.edu_client.find_order_by_no(ctx.user_id, order_no)
    if row is None:
        raise EduDataApiError("ORDER_NOT_FOUND", f"没有找到订单号 {order_no} 对应的订单，请核对后重试", 404)
    order_id = int(row["orderId"])
    detail = await ctx.edu_client.get_order(ctx.user_id, order_id)
    items = await ctx.order_item_adapter.list_order_items(int(ctx.user_id), order_id)
    return {"order": row, "detail": detail, "items": items}


def _render_order_summary(order: dict, detail: dict, items: list[dict]) -> str:
    """渲染订单展示文本：状态、金额、支付情况、报名课程。"""
    status = ORDER_STATUS_LABELS.get(order.get("orderStatusCode", ""), order.get("orderStatusCode", ""))
    lines = [
        f"订单 {order.get('orderNo')}（{status}）：",
        f"- 订单总额 ¥{detail.get('totalAmount')}，优惠 ¥{detail.get('discountAmount')}，应付 ¥{detail.get('payableAmount')}",
    ]
    paid = detail.get("paidAmount")
    if paid not in (None, "", "0", "0.00"):
        lines.append(f"- 已支付 ¥{paid}")
    payment = detail.get("paymentSummary") or {}
    if payment.get("paidAt"):
        lines.append(f"- 支付时间：{payment['paidAt']}")
    refund = detail.get("refundSummary") or {}
    if refund.get("refundRequestCount"):
        lines.append(f"- 已有 {refund['refundRequestCount']} 笔退款申请，退款合计 ¥{refund.get('refundAmount') or 0}")
    if items:
        item_lines = [
            f"  · {item.get('item_name')}"
            f"（{ORDER_ITEM_STATUS_LABELS.get(str(item.get('order_item_status')), item.get('order_item_status'))}，"
            f"¥{item.get('payable_amount')}）"
            for item in items
        ]
        lines.append("- 报名课程：\n" + "\n".join(item_lines))
    else:
        lines.append("- 该订单暂无报名明细")
    return "\n".join(lines)


@register_action("action_resolve_order")
class ResolveOrderAction:
    """订单状态查询流程：收集 order_no 后解析订单全貌。"""

    async def run(self, ctx: ActionContext) -> ActionResult:
        order_no = str(ctx.slots.get("order_no", "")).strip()
        if not order_no:
            return ActionResult(clear_slots=["order_no"])
        try:
            resolved = await _resolve_order(ctx, order_no)
        except EduDataApiError as exc:
            return ActionResult(slots_update={"resolve_failed": exc.message})
        except EduDataNetworkError as exc:
            return ActionResult(slots_update={"resolve_failed": exc.message})

        summary = _render_order_summary(resolved["order"], resolved["detail"], resolved["items"])
        return ActionResult(
            slots_update={
                "order_id": int(resolved["order"]["orderId"]),
                "order_status_text": ORDER_STATUS_LABELS.get(
                    resolved["order"].get("orderStatusCode", ""), resolved["order"].get("orderStatusCode", "")
                ),
                "order_summary": summary,
            }
        )


@register_action("action_resolve_order_for_write")
class ResolveOrderForWriteAction:
    """退款/工单流程：解析订单并准备明细选择。

    - 单明细：自动选定（order_item_id/order_item_name），不替用户默认；
      多明细：写入候选列表文本，等待用户选择。
    - 校验归属：明细来自只读适配器（按 user_id 过滤），不可跨用户复用。
    """

    async def run(self, ctx: ActionContext) -> ActionResult:
        order_no = str(ctx.slots.get("order_no", "")).strip()
        if not order_no:
            return ActionResult(clear_slots=["order_no"])
        try:
            resolved = await _resolve_order(ctx, order_no)
        except EduDataApiError as exc:
            return ActionResult(slots_update={"resolve_failed": exc.message})
        except EduDataNetworkError as exc:
            return ActionResult(slots_update={"resolve_failed": exc.message})

        order_id = int(resolved["order"]["orderId"])
        items = resolved["items"]
        updates: dict = {
            "order_id": order_id,
            "order_no_confirmed": resolved["order"].get("orderNo", order_no),
            "order_item_count": len(items),
            "order_items": [
                {
                    "order_item_id": int(item["id"]),
                    "item_name": item.get("item_name"),
                    "status": str(item.get("order_item_status")),
                    "payable_amount": str(item.get("payable_amount")),
                }
                for item in items
            ],
        }
        if len(items) == 1:
            item = items[0]
            updates["order_item_id"] = int(item["id"])
            updates["order_item_name"] = str(item.get("item_name"))
            updates["order_item_status"] = str(item.get("order_item_status"))
        elif len(items) > 1:
            updates["order_item_choices_text"] = format_order_items(items)
        return ActionResult(slots_update=updates)


@register_action("action_select_order_item")
class SelectOrderItemAction:
    """用户选择订单明细：支持序号或名称匹配；不匹配时清空重问。"""

    async def run(self, ctx: ActionContext) -> ActionResult:
        slots = ctx.slots
        items = slots.get("order_items") or []
        if not isinstance(items, list) or not items:
            return ActionResult(slots_update={"resolve_failed": "订单明细数据缺失，请重新提供订单号"})
        if len(items) == 1:
            item = items[0]
            return ActionResult(
                slots_update={
                    "order_item_id": int(item["order_item_id"]),
                    "order_item_name": str(item.get("item_name")),
                    "order_item_status": str(item.get("status")),
                }
            )

        choice = str(slots.get("order_item_choice", "")).strip()
        if not choice:
            return ActionResult()

        # 序号匹配（纯数字只按序号处理，不落入名称匹配，避免"5"命中"第5期"）
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(items):
                item = items[index]
                return ActionResult(
                    slots_update={
                        "order_item_id": int(item["order_item_id"]),
                        "order_item_name": str(item.get("item_name")),
                        "order_item_status": str(item.get("status")),
                    },
                    clear_slots=["order_item_choice"],
                )
        else:
            # 名称模糊匹配（唯一命中才有效）
            matched = [
                item
                for item in items
                if choice.lower() in str(item.get("item_name", "")).lower()
            ]
            if len(matched) == 1:
                item = matched[0]
                return ActionResult(
                    slots_update={
                        "order_item_id": int(item["order_item_id"]),
                        "order_item_name": str(item.get("item_name")),
                        "order_item_status": str(item.get("status")),
                    },
                    clear_slots=["order_item_choice"],
                )
        # 不匹配：清空选择，回到 collect 步骤重问
        return ActionResult(
            clear_slots=["order_item_choice"],
            slots_update={"order_item_choice_invalid": "未能识别你的选择，请回复明细对应的序号（如 1 或 2）。"},
        )
