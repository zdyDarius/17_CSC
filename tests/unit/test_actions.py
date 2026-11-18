"""Action 归一化与选择逻辑测试（不依赖网络/LLM）。"""

from __future__ import annotations

import asyncio

import pytest

import edu_assistant.task.action.edu.order_actions  # noqa: F401
import edu_assistant.task.action.edu.progress_actions  # noqa: F401
import edu_assistant.task.action.edu.refund_actions  # noqa: F401
import edu_assistant.task.action.edu.ticket_actions  # noqa: F401
from edu_assistant.domain.message import UserMessage
from edu_assistant.domain.state import DialogueState, TaskInstance
from edu_assistant.integrations.edu_data.client import EduDataClient
from edu_assistant.integrations.edu_data.order_item_adapter import OrderItemAdapter
from edu_assistant.task.action.base import ActionContext
from edu_assistant.task.action.registry import get_action


def make_ctx(slots: dict, text: str = "") -> ActionContext:
    state = DialogueState(sender_id="1001")
    state.tasks.active = TaskInstance(flow_id="ticket_submit", flow_name="工单提交", slots=slots)
    return ActionContext(
        state=state,
        user_message=UserMessage(sender_id="1001", message_id="m", session_id="s", text=text),
        settings=None,
        edu_client=None,
        order_item_adapter=None,
    )


# —— 工单类型归一 ——

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("投诉", "complaint"),
        ("投诉反馈", "complaint"),
        ("退款", "refund"),
        ("售后", "after_sales"),
        ("2", "complaint"),
        ("refund", "refund"),
    ],
)
def test_normalize_ticket_type(raw, expected):
    ctx = make_ctx({"ticket_type_raw": raw})
    result = asyncio.run(get_action("action_normalize_ticket_type").run(ctx))
    assert result.slots_update["ticket_type"] == expected


def test_normalize_ticket_type_invalid_clears():
    ctx = make_ctx({"ticket_type_raw": "随便"})
    result = asyncio.run(get_action("action_normalize_ticket_type").run(ctx))
    assert "ticket_type" in result.clear_slots
    assert result.slots_update["ticket_type_invalid"]


# —— 退款类型归一 ——

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("个人原因", "personal_reason"),
        ("没时间学", "personal_reason"),
        ("课程质量不行", "course_unsatisfied"),
        ("时间冲突", "schedule_conflict"),
        ("买重了", "duplicate_purchase"),
        ("personal_reason", "personal_reason"),
    ],
)
def test_normalize_refund_type(raw, expected):
    ctx = make_ctx({"refund_type_raw": raw})
    result = asyncio.run(get_action("action_normalize_refund_type").run(ctx))
    assert result.slots_update["refund_type"] == expected


def test_normalize_refund_type_invalid():
    ctx = make_ctx({"refund_type_raw": "abc"})
    result = asyncio.run(get_action("action_normalize_refund_type").run(ctx))
    assert "refund_type" in result.clear_slots
    assert result.slots_update["refund_type_invalid"]


# —— 明细选择 ——

ITEMS = [
    {"order_item_id": 11, "item_name": "Python 全栈第5期", "status": "paid", "payable_amount": "4800.00"},
    {"order_item_id": 12, "item_name": "数据分析实战第2期", "status": "paid", "payable_amount": "3600.00"},
]


def test_select_item_by_number():
    ctx = make_ctx({"order_items": ITEMS, "order_item_choice": "2"})
    result = asyncio.run(get_action("action_select_order_item").run(ctx))
    assert result.slots_update["order_item_id"] == 12
    assert result.slots_update["order_item_name"] == "数据分析实战第2期"


def test_select_item_by_name():
    ctx = make_ctx({"order_items": ITEMS, "order_item_choice": "数据分析实战第2期"})
    result = asyncio.run(get_action("action_select_order_item").run(ctx))
    assert result.slots_update["order_item_id"] == 12


def test_select_item_invalid_clears_and_reasks():
    ctx = make_ctx({"order_items": ITEMS, "order_item_choice": "5"})
    result = asyncio.run(get_action("action_select_order_item").run(ctx))
    assert "order_item_choice" in result.clear_slots
    assert result.slots_update["order_item_choice_invalid"]


def test_select_item_single_auto_picks():
    ctx = make_ctx({"order_items": ITEMS[:1], "order_item_choice": ""})
    result = asyncio.run(get_action("action_select_order_item").run(ctx))
    assert result.slots_update["order_item_id"] == 11


# —— 确认解析 ——

@pytest.mark.parametrize(("confirm", "expected"), [("确认", "confirmed"), ("取消", "cancelled"), ("1", "confirmed"), ("2", "cancelled"), ("no", "cancelled")])
def test_parse_confirm(confirm, expected):
    from edu_assistant.task.action.edu.refund_actions import parse_confirm

    assert parse_confirm(confirm) == expected


def test_parse_confirm_garbage():
    from edu_assistant.task.action.edu.refund_actions import parse_confirm

    assert parse_confirm("随便") == ""


# —— 工单标题规则 ——

def test_ticket_title_rule():
    from edu_assistant.task.action.edu.ticket_actions import build_ticket_title

    title = build_ticket_title("complaint", "第三章视频一直加载不出来，看了三遍都卡在同一位置")
    assert title.startswith("【投诉反馈】")
    assert len(title) <= 30


# —— 退款提交：确认缺失时重问 ——

def test_submit_refund_without_confirm_reasks():
    ctx = make_ctx(
        {
            "order_item_id": 11,
            "refund_type": "personal_reason",
            "refund_reason": "没时间",
            "refund_amount": "4800.00",
            "confirm_refund": "嗯嗯",
        }
    )
    result = asyncio.run(get_action("action_submit_refund").run(ctx))
    assert "confirm_refund" in result.clear_slots
    assert result.slots_update["confirm_invalid"]


def test_submit_refund_cancelled():
    ctx = make_ctx(
        {
            "order_item_id": 11,
            "refund_type": "personal_reason",
            "refund_reason": "没时间",
            "refund_amount": "4800.00",
            "confirm_refund": "取消",
        }
    )
    result = asyncio.run(get_action("action_submit_refund").run(ctx))
    assert result.slots_update.get("refund_cancelled") is True


# —— 退款类工单无退款申请 → 自动切换退款流程 ——

def test_start_refund_for_ticket_auto_commands():
    ctx = make_ctx({"order_no": "ORD20240401005", "ticket_type": "refund"})
    ctx.state.tasks.active.wait_refund_link = False
    result = asyncio.run(get_action("action_start_refund_for_ticket").run(ctx))
    assert ctx.state.tasks.active.wait_refund_link is True
    assert result.resume_step_id == "check_refund"
    assert [c.command for c in result.auto_commands] == ["start_flow", "set_slots"]
    assert result.auto_commands[1].slots["order_no"] == "ORD20240401005"
