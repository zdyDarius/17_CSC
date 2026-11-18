"""任务状态管控测试：切换/恢复/取消/退款转工单恢复。"""

from __future__ import annotations

from edu_assistant.domain.state import DialogueState, TaskInstance
from edu_assistant.lifecycle.models import TaskResumed, TaskStarted, TaskSwitched


def make_task(flow_id: str, name: str = "") -> TaskInstance:
    return TaskInstance(flow_id=flow_id, flow_name=name, step_id="start", slots={})


def test_start_and_complete():
    state = DialogueState(sender_id="1001")
    task = make_task("order_status_query", "订单状态查询")
    event = state.tasks.start_task(task)
    assert isinstance(event, TaskStarted)
    completed = state.tasks.complete_active()
    assert completed is not None
    assert state.tasks.active is None


def test_switch_pushes_stack():
    state = DialogueState(sender_id="1001")
    first = make_task("order_status_query", "订单查询")
    state.tasks.start_task(first)
    second = make_task("refund_request", "退款申请")
    event = state.tasks.start_task(second)
    assert isinstance(event, TaskSwitched)
    assert state.tasks.active is second
    assert state.tasks.paused == [first]


def test_switch_overflow_returns_none():
    state = DialogueState(sender_id="1001")
    for i in range(6):
        task = make_task("refund_request", f"退款{i}")
        event = state.tasks.start_task(task, max_paused=5)
        if i == 0:
            assert event is not None
    # 第 7 个任务时栈满 → None
    assert state.tasks.start_task(make_task("ticket_submit"), max_paused=5) is None


def test_resume_pops_stack():
    state = DialogueState(sender_id="1001")
    first = make_task("order_status_query", "订单查询")
    state.tasks.start_task(first)
    second = make_task("refund_request", "退款申请")
    state.tasks.start_task(second)
    # 完成第二个 → 栈顶是第一个；恢复它
    state.tasks.complete_active()
    event = state.tasks.resume_task(first.task_id)
    assert isinstance(event, TaskResumed)
    assert state.tasks.active is first
    assert state.tasks.paused == []


def test_resume_unknown_returns_none():
    state = DialogueState(sender_id="1001")
    assert state.tasks.resume_task("nonexistent") is None


def test_cancel_active():
    state = DialogueState(sender_id="1001")
    task = make_task("refund_request", "退款申请")
    state.tasks.start_task(task)
    event = state.tasks.cancel_task(task.task_id)
    assert event is not None
    assert state.tasks.active is None


def test_cancel_unknown_returns_none():
    state = DialogueState(sender_id="1001")
    assert state.tasks.cancel_task("nope") is None
