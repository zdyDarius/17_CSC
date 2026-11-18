"""状态序列化与引擎装配冒烟测试。"""

from __future__ import annotations

from pathlib import Path

from edu_assistant.domain.state import (
    DialogueState,
    SessionCache,
    TaskInstance,
)
from edu_assistant.repository.dialogue_repository import state_from_json, state_to_json
from edu_assistant.conf.config import get_settings


def make_state() -> DialogueState:
    state = DialogueState(sender_id="1001", session_id="sess-1")
    state.session_cache = SessionCache(student_id=7)
    task = TaskInstance(
        flow_id="refund_request",
        flow_name="退款申请",
        step_id="ask_refund_type",
        task_id="t-1",
        slots={"order_no": "ORD20240401005", "order_item_id": 12},
        wait_refund_link=False,
        resume_step_id="",
    )
    state.tasks.active = task
    paused = TaskInstance(
        flow_id="ticket_submit",
        flow_name="工单提交",
        step_id="check_refund",
        task_id="t-2",
        slots={"ticket_type": "refund"},
        wait_refund_link=True,
    )
    state.tasks.paused.append(paused)
    state.history.append({"role": "user", "content": "我要退款"})
    return state


def test_state_roundtrip():
    state = make_state()
    raw = state_to_json(state)
    loaded = state_from_json(raw, "1001", "sess-1")
    assert loaded.sender_id == "1001"
    assert loaded.session_cache.student_id == 7
    assert loaded.tasks.active is not None
    assert loaded.tasks.active.flow_id == "refund_request"
    assert loaded.tasks.active.slots["order_no"] == "ORD20240401005"
    assert loaded.tasks.paused[0].wait_refund_link is True
    assert loaded.tasks.paused[0].task_id == "t-2"
    assert loaded.history[0]["content"] == "我要退款"


def test_state_json_is_loadable_json():
    raw = state_to_json(make_state())
    import json

    data = json.loads(raw)
    # 状态版本（乐观锁）随 JSON 持久化；旧状态反序列化时未知字段走默认值
    assert data["state_version"] >= 0


def test_engine_builder_smoke(tmp_path, monkeypatch):
    """真实流程配置 + 注册 Action 可装配（不启动外部资源）。"""
    import edu_assistant.task.action.edu.order_actions  # noqa: F401
    import edu_assistant.task.action.edu.progress_actions  # noqa: F401
    import edu_assistant.task.action.edu.refund_actions  # noqa: F401
    import edu_assistant.task.action.edu.ticket_actions  # noqa: F401
    from edu_assistant.engine.builder import build_engine

    components = build_engine(get_settings())
    assert components.engine is not None
    assert set(components.flow_catalog.flows) == {
        "order_status_query",
        "learning_progress_query",
        "refund_request",
        "ticket_submit",
    }
