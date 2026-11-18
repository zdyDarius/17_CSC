"""教育业务 Action：工单提交与退款转工单。"""

from __future__ import annotations

from edu_assistant.integrations.edu_data.order_item_adapter import REFUND_STATUS_LABELS
from edu_assistant.plan.command import SetSlotsCommand, StartFlowCommand
from edu_assistant.task.action.base import ActionContext, ActionResult
from edu_assistant.task.action.edu.refund_actions import parse_confirm
from edu_assistant.task.action.registry import register_action
from edu_assistant.utils.errors import EduDataApiError, EduDataNetworkError
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

TICKET_TYPE_LABELS = {
    "after_sales": "售后咨询",
    "complaint": "投诉反馈",
    "refund": "退费申请",
}

TICKET_TYPE_ALIASES = {
    "after_sales": "after_sales",
    "售后": "after_sales",
    "售后咨询": "after_sales",
    "售后问题": "after_sales",
    "complaint": "complaint",
    "投诉": "complaint",
    "投诉反馈": "complaint",
    "举报": "complaint",
    "refund": "refund",
    "退款": "refund",
    "退费": "refund",
    "退订": "refund",
}

DEFAULT_PRIORITY = "medium"
TICKET_SOURCE = "user_app"


def _map_alias(raw: str, aliases: dict[str, str]) -> str | None:
    """别名归一：精确 → 小写精确 → 包含式匹配（别名是输入子串且枚举唯一）。"""
    mapped = aliases.get(raw) or aliases.get(raw.lower())
    if mapped:
        return mapped
    hits = {value for alias, value in aliases.items() if alias and alias in raw}
    return hits.pop() if len(hits) == 1 else None


async def _ensure_student_id(ctx: ActionContext) -> int:
    """学员档案 ID：工单提交前按需重新校验（4.1），结果同步会话缓存。"""
    profile = await ctx.edu_client.get_student_profile(ctx.user_id)
    student_id = int(profile["studentId"])
    ctx.state.session_cache.student_id = student_id
    ctx.state.session_cache.student_profile = {
        "identity": (profile.get("learnerIdentity") or {}).get("name"),
        "goal": (profile.get("learningGoal") or {}).get("name"),
    }
    return student_id


@register_action("action_normalize_ticket_type")
class NormalizeTicketTypeAction:
    """工单类型归一：中文/序号 → 固定枚举。"""

    async def run(self, ctx: ActionContext) -> ActionResult:
        raw = str(
            ctx.slots.get("ticket_type_raw", "") or ctx.slots.get("ticket_type", "")
        ).strip()
        index_map = {"1": "after_sales", "2": "complaint", "3": "refund"}
        mapped = index_map.get(raw) or _map_alias(raw, TICKET_TYPE_ALIASES)
        if mapped:
            return ActionResult(slots_update={"ticket_type": mapped}, clear_slots=["ticket_type_raw"])
        return ActionResult(
            clear_slots=["ticket_type", "ticket_type_raw"],
            slots_update={"ticket_type_invalid": "请选择工单类型：1 售后咨询、2 投诉反馈、3 退费申请（回复序号或名称）。"},
        )


@register_action("action_check_refund_for_ticket")
class CheckRefundForTicketAction:
    """退款类工单：校验已有退款申请并与订单明细匹配。

    - 已有匹配明细的申请 → 写入 refund_request_id，继续工单流程。
    - 没有 → 由后续步骤启动退款流程（暂存工单）。
    - 非退款类工单 → 直接继续。
    """

    async def run(self, ctx: ActionContext) -> ActionResult:
        slots = ctx.slots
        if str(slots.get("ticket_type", "")) != "refund":
            return ActionResult(slots_update={"ticket_type_label": TICKET_TYPE_LABELS.get(str(slots.get("ticket_type", "")), "")})
        order_item_id = slots.get("order_item_id")
        if not order_item_id:
            return ActionResult(slots_update={"resolve_failed": "订单明细数据缺失，请重新提供订单号"})
        try:
            refunds = await ctx.order_item_adapter.list_refunds_for_order(
                int(ctx.user_id), int(slots.get("order_id", 0))
            )
        except EduDataApiError as exc:
            return ActionResult(slots_update={"resolve_failed": exc.message})
        matched = [
            r for r in refunds
            if int(r.get("order_item_id", 0)) == int(order_item_id)
            and str(r.get("refund_status")) in ("pending", "approved", "refunded")
        ]
        if matched:
            refund = matched[0]
            return ActionResult(
                slots_update={
                    "refund_request_id": int(refund["id"]),
                    "refund_no": str(refund.get("refund_no", "")),
                    "existing_refund_text": (
                        f"已关联该明细的退款申请 {refund.get('refund_no')}"
                        f"（{REFUND_STATUS_LABELS.get(str(refund.get('refund_status')), refund.get('refund_status'))}）"
                    ),
                }
            )
        return ActionResult(clear_slots=["refund_request_id"])


@register_action("action_start_refund_for_ticket")
class StartRefundForTicketAction:
    """退款类工单无申请时：暂存工单任务并自动启动退款流程（复用订单号）。

    工单任务标记 wait_refund_link=True；退款流程成功后由 end 步骤自动恢复
    并把 refund_request_id 带回工单任务（重新执行检查步骤）。
    """

    async def run(self, ctx: ActionContext) -> ActionResult:
        task = ctx.state.tasks.active
        if task is None:
            return ActionResult(slots_update={"resolve_failed": "当前没有进行中的工单任务"})
        task.wait_refund_link = True
        # 退款流程复用 order_no（用户可见订单号）；明细在退款流程中重新解析与确认
        # resume_step_id：退款完成后自动恢复工单任务时回到 check_refund 重新校验
        return ActionResult(
            slots_update={
                "paused_for_refund_text": (
                    "退款类工单必须先关联退款申请。已为你切换到退款申请流程（沿用订单号，稍后会请你确认明细与金额），"
                    "退款申请提交后我将自动继续你的工单。"
                )
            },
            resume_step_id="check_refund",
            auto_commands=[
                StartFlowCommand(command="start_flow", flow="refund_request"),
                SetSlotsCommand(
                    command="set_slots",
                    slots={"order_no": str(ctx.slots.get("order_no", ""))},
                ),
            ],
        )


def build_ticket_title(ticket_type: str, description: str) -> str:
    """标题由类型和描述按确定规则生成（项目计划 4.2）。"""
    label = TICKET_TYPE_LABELS.get(ticket_type, ticket_type)
    desc = description.strip().replace("\n", " ")
    if len(desc) > 20:
        desc = desc[:20] + "…"
    return f"【{label}】{desc}"


@register_action("action_submit_ticket")
class SubmitTicketAction:
    """提交工单：解析确认 → 调工单 API。

    - studentId 取学员档案；ticketSource=user_app；优先级默认 medium。
    - 退款类工单必须携带 refund_request_id（与明细匹配）。
    - POST 网络结果不明时不自动重发，提示核对。
    """

    async def run(self, ctx: ActionContext) -> ActionResult:
        slots = ctx.slots
        confirm = parse_confirm(slots.get("confirm_ticket"))
        if confirm == "cancelled":
            return ActionResult(slots_update={"ticket_cancelled": True})
        if confirm != "confirmed":
            return ActionResult(
                clear_slots=["confirm_ticket"],
                slots_update={"confirm_invalid": "请回复「确认」提交工单，或「取消」放弃。"},
            )

        ticket_type = str(slots.get("ticket_type", ""))
        order_item_id = slots.get("order_item_id")
        description = str(slots.get("issue_description", "")).strip()
        if not (ticket_type and order_item_id and description):
            return ActionResult(slots_update={"resolve_failed": "工单信息不完整，请重新发起"})

        try:
            student_id = await _ensure_student_id(ctx)
        except (EduDataApiError, EduDataNetworkError) as exc:
            return ActionResult(slots_update={"ticket_error": f"学员档案查询失败：{exc.message}"})

        body = {
            "ticketType": ticket_type,
            "priorityLevel": DEFAULT_PRIORITY,
            "ticketSource": TICKET_SOURCE,
            "title": build_ticket_title(ticket_type, description),
            "ticketContent": description[:1000],
            "studentId": student_id,
            "orderItemId": int(order_item_id),
        }
        if ticket_type == "refund":
            refund_request_id = slots.get("refund_request_id")
            if not refund_request_id:
                return ActionResult(
                    slots_update={
                        "ticket_error": "退款类工单必须关联退款申请；请先完成退款申请后再提交工单。"
                    }
                )
            body["refundRequestId"] = int(refund_request_id)

        try:
            result = await ctx.edu_client.create_service_ticket(ctx.user_id, body)
        except EduDataApiError as exc:
            return ActionResult(slots_update={"ticket_error": exc.message, "_ticket_error_code": exc.code})
        except EduDataNetworkError:
            # 网络结果不明：按确定性标题查询已有工单核对，不自动重发（八.2）
            existing = await self._find_existing_ticket(ctx, body["title"])
            if existing is not None:
                return ActionResult(
                    slots_update={
                        "ticket_error": (
                            f"工单提交状态不确定，但查询到疑似已提交的工单"
                            f"（{existing.get('ticketNo')}，状态 {existing.get('ticketStatus')}），"
                            f"请以此为准确认，勿重复提交。"
                        )
                    }
                )
            return ActionResult(
                slots_update={
                    "ticket_error": "工单提交超时，结果不确定。为避免重复建单，暂不自动重试；请稍后在工单列表中核对或联系人工客服。"
                }
            )

        return ActionResult(
            slots_update={
                "ticket_id": int(result.get("ticketId", 0)),
                "ticket_no": str(result.get("ticketNo", "")),
                "ticket_status": str(result.get("ticketStatus", "pending")),
            }
        )

    async def _find_existing_ticket(self, ctx: ActionContext, title: str) -> dict | None:
        """按确定性标题匹配最近工单（POST 结果不明时核对，防重复建单）。"""
        try:
            result = await ctx.edu_client.list_service_tickets(ctx.user_id)
        except Exception:  # noqa: BLE001
            return None
        for row in result.get("list", []):
            if str(row.get("title", "")) == title:
                return row
        return None
