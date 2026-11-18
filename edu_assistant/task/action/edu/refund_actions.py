"""教育业务 Action：退款申请。"""

from __future__ import annotations

from edu_assistant.integrations.edu_data.order_item_adapter import REFUND_STATUS_LABELS
from edu_assistant.task.action.base import ActionContext, ActionResult
from edu_assistant.task.action.registry import register_action
from edu_assistant.utils.errors import EduDataApiError, EduDataNetworkError
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

REFUND_TYPE_LABELS = {
    "personal_reason": "个人原因",
    "course_unsatisfied": "课程不满意",
    "schedule_conflict": "时间冲突",
    "duplicate_purchase": "重复购买",
}

# 中文/口语别名 → 固定枚举（不能自由生成）
REFUND_TYPE_ALIASES = {
    "personal_reason": "personal_reason",
    "个人原因": "personal_reason",
    "个人安排": "personal_reason",
    "没时间": "personal_reason",
    "没时间学": "personal_reason",
    "时间不够": "personal_reason",
    "course_unsatisfied": "course_unsatisfied",
    "课程不满意": "course_unsatisfied",
    "不满意": "course_unsatisfied",
    "课程质量": "course_unsatisfied",
    "不适合": "course_unsatisfied",
    "schedule_conflict": "schedule_conflict",
    "时间冲突": "schedule_conflict",
    "安排冲突": "schedule_conflict",
    "档期冲突": "schedule_conflict",
    "排课冲突": "schedule_conflict",
    "duplicate_purchase": "duplicate_purchase",
    "重复购买": "duplicate_purchase",
    "重复下单": "duplicate_purchase",
    "买重了": "duplicate_purchase",
    "重复了": "duplicate_purchase",
}


def parse_confirm(text: str | None) -> str:
    """解析确认/取消。返回 confirmed / cancelled / 空。"""
    if not text:
        return ""
    value = str(text).strip().lower()
    if value in {"1", "确认", "确定", "是", "对", "好的", "好", "yes", "y", "继续", "同意", "退"}:
        return "confirmed"
    if value in {"2", "取消", "不了", "不", "否", "no", "n", "算了", "不退", "先不"}:
        return "cancelled"
    return ""


def _map_alias(raw: str, aliases: dict[str, str]) -> str | None:
    """别名归一：精确 → 小写精确 → 包含式匹配（别名是输入子串且枚举唯一）。"""
    mapped = aliases.get(raw) or aliases.get(raw.lower())
    if mapped:
        return mapped
    hits = {value for alias, value in aliases.items() if alias and alias in raw}
    return mapped or (hits.pop() if len(hits) == 1 else None)


@register_action("action_normalize_refund_type")
class NormalizeRefundTypeAction:
    """退款类型归一：LLM 输出或用户中文 → 固定枚举；无法映射时清空重问。

    原因字段（API 必填）同步生成：用户原话比类型名更具体时用原话，否则用类型名。
    """

    async def run(self, ctx: ActionContext) -> ActionResult:
        raw = str(ctx.slots.get("refund_type_raw", "") or ctx.slots.get("refund_type", "") or "").strip()
        if not raw:
            return ActionResult(clear_slots=["refund_type"])
        index_map = {
            "1": "personal_reason",
            "2": "course_unsatisfied",
            "3": "schedule_conflict",
            "4": "duplicate_purchase",
        }
        mapped = index_map.get(raw) or _map_alias(raw, REFUND_TYPE_ALIASES)
        if mapped:
            label = REFUND_TYPE_LABELS.get(mapped, mapped)
            # 原因：用户原话（非序号/非枚举名且更长）→ 原话；否则用类型名
            reason = label
            if raw and not raw.isdigit() and raw.lower() != mapped and len(raw) > len(label):
                reason = raw
            return ActionResult(
                slots_update={
                    "refund_type": mapped,
                    "refund_reason": reason,
                },
                clear_slots=["refund_type_raw"],
            )
        return ActionResult(
            clear_slots=["refund_type", "refund_type_raw"],
            slots_update={"refund_type_invalid": "请从以下类型中选择：1 个人原因、2 课程不满意、3 时间冲突、4 重复购买（回复序号或名称）。"},
        )


@register_action("action_list_refunds")
class ListRefundsAction:
    """退款进度查询：列出学员全部退款申请；可选按 RF 编号过滤。"""

    async def run(self, ctx: ActionContext) -> ActionResult:
        refund_no = str(ctx.slots.get("refund_no", "") or "").strip()
        try:
            result = await ctx.edu_client.list_refund_requests(ctx.user_id)
        except (EduDataApiError, EduDataNetworkError) as exc:
            return ActionResult(slots_update={"query_failed": exc.message})
        rows = result.get("list", []) or []
        if refund_no:
            rows = [r for r in rows if refund_no.upper() in str(r.get("refundNo", "")).upper()]
        if not rows:
            hint = f"编号包含 {refund_no} 的" if refund_no else ""
            summary = f"没有找到{hint}退款申请记录。"
        else:
            lines = []
            for r in rows[:8]:
                status = REFUND_STATUS_LABELS.get(
                    str(r.get("refundStatusCode", "")), str(r.get("refundStatusCode", ""))
                )
                applied = str(r.get("appliedAt") or "")[:10]
                line = f"· {r.get('refundNo')}（{status}）申请 ¥{r.get('applyAmount')}"
                approved = r.get("approvedAmount")
                if approved not in (None, "", "0.00"):
                    line += f"，核定 ¥{approved}"
                if applied:
                    line += f"，申请于 {applied}"
                lines.append(line)
            summary = f"你的退款申请共 {len(rows)} 笔：\n" + "\n".join(lines)
        return ActionResult(slots_update={"refunds_summary": summary})


@register_action("action_preview_refund")
class PreviewRefundAction:
    """退款确认页：展示明细与全额退款金额（金额由系统计算，不由 LLM 决定）。"""

    async def run(self, ctx: ActionContext) -> ActionResult:
        slots = ctx.slots
        order_item_id = slots.get("order_item_id")
        items = slots.get("order_items") or []
        matched = None
        if order_item_id is not None:
            for item in items:
                if int(item.get("order_item_id", 0)) == int(order_item_id):
                    matched = item
                    break
        if matched is None:
            return ActionResult(slots_update={"resolve_failed": "订单明细数据缺失，请重新提供订单号"})

        status = str(matched.get("status", ""))
        if status not in ("paid", "completed"):
            from edu_assistant.integrations.edu_data.order_item_adapter import (
                ORDER_ITEM_STATUS_LABELS,
            )

            return ActionResult(
                slots_update={
                    "refund_not_allowed": (
                        f"订单明细「{matched.get('item_name')}」当前状态为"
                        f"「{ORDER_ITEM_STATUS_LABELS.get(status, status)}」，不允许发起退款。"
                    )
                }
            )

        amount = str(matched.get("payable_amount", "0"))
        refund_type = REFUND_TYPE_LABELS.get(str(slots.get("refund_type", "")), str(slots.get("refund_type", "")))
        reason = str(slots.get("refund_reason", "") or "").strip()
        reason_line = f"- 退款原因：{reason}\n" if reason else ""
        return ActionResult(
            slots_update={
                "refund_amount": amount,
                "refund_preview_text": (
                    f"请确认退款申请：\n"
                    f"- 订单：{slots.get('order_no_confirmed') or slots.get('order_no')}\n"
                    f"- 报名明细：{matched.get('item_name')}\n"
                    f"- 退款类型：{refund_type}\n"
                    f"{reason_line}"
                    f"- 退款金额：¥{amount}（该明细全额退款）\n"
                    f"回复「确认」提交申请，回复「取消」放弃。"
                ),
            }
        )


@register_action("action_submit_refund")
class SubmitRefundAction:
    """提交退款申请：解析确认 → 调退款 API。

    - 处理中退款/不可退款/金额超限等业务错误给出明确回复，不重试写请求。
    - POST 网络结果不明时不自动重发，先查询已有申请并提示核对。
    """

    async def run(self, ctx: ActionContext) -> ActionResult:
        slots = ctx.slots
        confirm = parse_confirm(slots.get("confirm_refund"))
        if confirm == "cancelled":
            return ActionResult(slots_update={"refund_cancelled": True})
        if confirm != "confirmed":
            return ActionResult(
                clear_slots=["confirm_refund"],
                slots_update={"confirm_invalid": "请回复「确认」提交退款申请，或「取消」放弃。"},
            )

        order_item_id = slots.get("order_item_id")
        refund_type = slots.get("refund_type")
        refund_reason = str(slots.get("refund_reason", "")).strip()
        amount = str(slots.get("refund_amount", "0"))
        if not (order_item_id and refund_type and refund_reason and amount):
            return ActionResult(slots_update={"resolve_failed": "退款信息不完整，请重新发起退款申请"})

        try:
            result = await ctx.edu_client.create_refund_request(
                user_id=ctx.user_id,
                order_item_id=int(order_item_id),
                refund_type=str(refund_type),
                refund_reason=refund_reason[:500],
                apply_amount=amount,
            )
        except EduDataApiError as exc:
            return ActionResult(slots_update={"refund_error": exc.message, "_refund_error_code": exc.code})
        except EduDataNetworkError:
            # 网络结果不明：查询已有申请，提示核对，不自动重发
            existing = await self._find_existing_refund(ctx, int(order_item_id))
            if existing is not None:
                return ActionResult(
                    slots_update={
                        "refund_error": (
                            f"退款申请提交状态不确定，但查询到该明细已存在退款申请"
                            f"（{existing.get('refund_no')}，状态 {existing.get('refund_status')}），"
                            f"请以此为准确认，勿重复提交。"
                        )
                    }
                )
            return ActionResult(
                slots_update={
                    "refund_error": "退款申请提交超时，结果不确定。为避免重复退款，暂不自动重试；请稍后重新查询退款状态或联系人工客服核实。"
                }
            )

        return ActionResult(
            slots_update={
                "refund_request_id": int(result.get("refundRequestId", 0)),
                "refund_no": str(result.get("refundNo", "")),
                "refund_status": str(result.get("refundStatusCode", "pending")),
            }
        )

    async def _find_existing_refund(self, ctx: ActionContext, order_item_id: int) -> dict | None:
        try:
            refunds = await ctx.order_item_adapter.list_refunds_for_order(
                int(ctx.user_id), int(ctx.slots.get("order_id", 0))
            )
        except Exception:  # noqa: BLE001
            return None
        for refund in refunds:
            if int(refund.get("order_item_id", 0)) == order_item_id:
                return refund
        return None
