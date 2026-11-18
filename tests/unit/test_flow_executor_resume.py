"""FlowExecutor 恢复语义测试（4.3）：新任务结束后自动恢复栈顶。

使用假 Action（不依赖网络/LLM），直接驱动状态机。
"""

from __future__ import annotations

import pytest

import edu_assistant.task.action.edu.order_actions  # noqa: F401
import edu_assistant.task.action.edu.progress_actions  # noqa: F401
import edu_assistant.task.action.edu.refund_actions  # noqa: F401
import edu_assistant.task.action.edu.ticket_actions  # noqa: F401
from edu_assistant.conf.config import get_settings
from edu_assistant.domain.message import UserMessage
from edu_assistant.domain.state import DialogueState, TaskInstance
from edu_assistant.task.action.action_runner import ActionRunner
from edu_assistant.task.action.base import ActionContext
from edu_assistant.task.action.registry import get_action
from edu_assistant.task.command.command_processor import CommandProcessor
from edu_assistant.task.flow.loader import FlowLoader
from edu_assistant.task.flowexecutor.flow_executor import FlowExecutor
from edu_assistant.task.response.response_render import ResponseRender
from edu_assistant.integrations.edu_data.client import EduDataClient  # noqa: F401
from edu_assistant.integrations.edu_data.order_item_adapter import OrderItemAdapter  # noqa: F401


from edu_assistant.task.action.registry import registered_action_names


@pytest.fixture(scope="module")
def catalog():
    return FlowLoader().load(get_settings().flow_config_path, registered_action_names())


def make_user_message(text: str = "继续") -> UserMessage:
    return UserMessage(sender_id="1001", message_id="m", session_id="s", text=text)


class FakeActionRunner:
    """不执行真实 Action，仅推进流程用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(self, action_name: str, ctx) -> object:
        self.calls.append(action_name)
        from edu_assistant.task.action.base import ActionResult

        return ActionResult()


def make_executor(catalog, runner=None) -> FlowExecutor:
    runner = runner or FakeActionRunner()
    return (
        FlowExecutor(
            response_render=ResponseRender(),
            action_runner=runner,
            command_processor=CommandProcessor(),
            flow_catalog=catalog,
            max_steps=30,
        ),
        runner,
    )


def ctx_factory(state, user_message) -> ActionContext:
    return ActionContext(
        state=state,
        user_message=user_message,
        settings=get_settings(),
        edu_client=None,
        order_item_adapter=None,
    )


def test_end_auto_resumes_top_paused_task(catalog):
    """新任务完成后自动弹出栈顶并从保存步骤继续（4.3 恢复语义）。"""
    state = DialogueState(sender_id="1001", session_id="s")
    # 任务 A：退款流程，收集完 order_no 停在 ask_refund_type（等待类型选择）
    task_a = TaskInstance(
        flow_id="refund_request",
        flow_name="退款申请",
        step_id="ask_refund_type",
        slots={"order_no": "ORD20240401005", "refund_reason": "时间冲突"},
    )
    state.tasks.active = task_a

    # 任务 B：订单查询（简单流程），set_slots 已有 order_no → 一步走完
    task_b = TaskInstance(
        flow_id="order_status_query",
        flow_name="订单状态查询",
        step_id="start",
        slots={"order_no": "ORD20240401005"},
    )
    state.tasks.start_task(task_b)  # 压栈 A，激活 B

    executor, runner = make_executor(catalog)
    messages, events = asyncio_run(
        executor.execute(state, make_user_message("帮我查下订单"), ctx_factory)
    )

    # B 走到 end（resolve_order 会因 FakeActionRunner 不写 order_summary 而走到 show/end）
    assert state.tasks.active is not None
    # 自动恢复了栈顶 A，且 A 从保存的步骤继续
    assert state.tasks.active.task_id == task_a.task_id
    assert state.tasks.active.flow_id == "refund_request"
    assert state.tasks.paused == []
    # 恢复提示出现（继续刚才的「退款申请」）
    assert any("继续刚才" in m.text for m in messages)
    # A 的流程继续推进：ask_refund_type → 追问退款类型（refund_reason 已有 → 继续到类型选择）
    assert "退款类型" in messages[-1].text or any("退款类型" in m.text for m in messages)


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_end_auto_resume_writes_clear_confirm_slots(catalog):
    """恢复写操作流程时清空确认槽位，确认页重新展示关键对象（4.3）。"""
    state = DialogueState(sender_id="1001", session_id="s")
    task_a = TaskInstance(
        flow_id="refund_request",
        flow_name="退款申请",
        step_id="ask_confirm_refund",
        slots={
            "order_no": "ORD20240401005",
            "refund_reason": "时间冲突",
            "refund_type": "schedule_conflict",
            "confirm_refund": "确认",  # 恢复时应清空
            "refund_preview_text": "请确认退款申请……",
        },
    )
    state.tasks.active = task_a
    task_b = TaskInstance(
        flow_id="order_status_query",
        flow_name="订单状态查询",
        step_id="start",
        slots={"order_no": "ORD20240401005"},
    )
    state.tasks.start_task(task_b)

    executor, _ = make_executor(catalog)
    messages, _events = asyncio_run(
        executor.execute(state, make_user_message("帮我查下订单"), ctx_factory)
    )
    active = state.tasks.active
    assert active.task_id == task_a.task_id
    # 确认槽位被清空 → collect confirm 重新渲染确认页
    assert not active.slots.get("confirm_refund")
    assert any("请确认退款申请" in m.text for m in messages)


def test_wait_refund_link_resume_goes_to_recheck_step(catalog):
    """退款转工单：退款流程结束后恢复工单任务并回到 check_refund 重检。"""
    state = DialogueState(sender_id="1001", session_id="s")
    ticket_task = TaskInstance(
        flow_id="ticket_submit",
        flow_name="工单提交",
        step_id="start_refund_first",
        slots={
            "ticket_type": "refund",
            "order_no": "ORD20240401005",
            "order_item_id": 11,
            "refund_request_id": 99,  # 退款流程已带回申请
        },
        wait_refund_link=True,
        resume_step_id="check_refund",
    )
    state.tasks.active = ticket_task
    # 退款流程：预置槽位使其在 FakeActionRunner 下直接走到 end
    refund_task = TaskInstance(
        flow_id="refund_request",
        flow_name="退款申请",
        step_id="start",
        slots={
            "order_no": "ORD20240401005",
            "order_item_id": 11,
            "refund_reason": "时间冲突",
            "refund_type": "schedule_conflict",
            "confirm_refund": "确认",
        },
    )
    # 模拟 action_start_refund_for_ticket 的 auto_commands 已执行
    state.tasks.start_task(refund_task)

    executor, _ = make_executor(catalog)
    messages, _events = asyncio_run(
        executor.execute(state, make_user_message("好的"), ctx_factory)
    )
    # refund 流程 end 后自动恢复工单 → wait_refund_link 恢复到 check_refund 重检
    # 重检发现已有退款申请 → 继续收集问题描述 → 停在 ask_description
    active = state.tasks.active
    assert active is not None and active.task_id == ticket_task.task_id
    assert state.tasks.paused == []
    assert any("继续刚才" in m.text for m in messages)
    # 工单继续推进：check_refund 通过（refund_request_id 已有）→ 追问问题描述
    assert active.flow_id == "ticket_submit"
    assert any("描述" in m.text or "问题描述" in m.text for m in messages)
