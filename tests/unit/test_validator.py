"""TurnPlan 严格校验器测试（不依赖真实 LLM）。"""

from __future__ import annotations

import pytest

from edu_assistant.domain.message import UserMessage
from edu_assistant.domain.state import DialogueState, TaskInstance
from edu_assistant.knowledge.intents import intents_catalog
from edu_assistant.plan.command import (
    CancelTaskCommand,
    Command,
    ResumeTaskCommand,
    SetSlotsCommand,
    StartFlowCommand,
)
from edu_assistant.plan.turn_plan_validator import TurnPlanValidator, validate_object_message
from edu_assistant.prompts.models import (
    ChitchatTurnPlan,
    ClarifyReason,
    KnowledgeTurnPlan,
    TaskTurnPlan,
    TurnPlan,
)
from edu_assistant.task.flow.loader import FlowLoader

import edu_assistant.task.action.edu.order_actions  # noqa: F401
import edu_assistant.task.action.edu.progress_actions  # noqa: F401
import edu_assistant.task.action.edu.refund_actions  # noqa: F401
import edu_assistant.task.action.edu.ticket_actions  # noqa: F401
from edu_assistant.task.action.registry import registered_action_names
from edu_assistant.conf.config import get_settings


@pytest.fixture(scope="module")
def catalog():
    return FlowLoader().load(get_settings().flow_config_path, registered_action_names())


@pytest.fixture(scope="module")
def validator():
    return TurnPlanValidator(intents_catalog())


@pytest.fixture
def state() -> DialogueState:
    state = DialogueState(sender_id="1001")
    state.tasks.active = TaskInstance(
        flow_id="order_status_query",
        flow_name="订单状态查询",
        step_id="ask_order_no",
        task_id="t-1",
        slots={},
    )
    return state


def _msg(text="查订单") -> UserMessage:
    return UserMessage(sender_id="1001", message_id="m1", session_id="s1", text=text)


def test_empty_plan_rejected(validator, state, catalog):
    result = validator.validate(TurnPlan(), state, catalog)
    assert not result.valid
    assert result.reason == ClarifyReason.MISSING_TRACK


def test_multiple_tracks_rejected(validator, state, catalog):
    plan = TurnPlan(
        task=TaskTurnPlan(commands=[StartFlowCommand(command="start_flow", flow="refund_request")]),
        knowledge=KnowledgeTurnPlan(intents=["refund_policy"]),
    )
    result = validator.validate(plan, state, catalog)
    assert not result.valid
    assert result.reason == ClarifyReason.MULTIPLE_TRACKS


def test_valid_start_flow_with_slots(validator, state, catalog):
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[
                StartFlowCommand(command="start_flow", flow="order_status_query"),
                SetSlotsCommand(command="set_slots", slots={"order_no": "ORD20240401005"}),
            ]
        )
    )
    result = validator.validate(plan, state, catalog)
    assert result.valid


def test_unknown_flow_rejected(validator, state, catalog):
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[StartFlowCommand(command="start_flow", flow="nonexistent_flow")]
        )
    )
    result = validator.validate(plan, state, catalog)
    assert not result.valid
    assert result.reason == ClarifyReason.INVALID_TASK_COMMAND


def test_empty_commands_rejected(validator, state, catalog):
    plan = TurnPlan(task=TaskTurnPlan(commands=[]))
    result = validator.validate(plan, state, catalog)
    assert not result.valid
    assert result.reason == ClarifyReason.MISSING_TASK_COMMANDS


def test_set_slots_unknown_slot_rejected(validator, state, catalog):
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[SetSlotsCommand(command="set_slots", slots={"bad_slot": "x"})]
        )
    )
    result = validator.validate(plan, state, catalog)
    assert not result.valid
    assert result.reason == ClarifyReason.INVALID_TASK_COMMAND


def test_set_slots_without_active_rejected(validator, catalog):
    empty_state = DialogueState(sender_id="1001")
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[SetSlotsCommand(command="set_slots", slots={"order_no": "ORD1"})]
        )
    )
    result = validator.validate(plan, empty_state, catalog)
    assert not result.valid


def test_resume_unknown_task_rejected(validator, state, catalog):
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[ResumeTaskCommand(command="resume_task", task_id="ghost")]
        )
    )
    result = validator.validate(plan, state, catalog)
    assert not result.valid


def test_resume_paused_task_ok(validator, state, catalog):
    paused = TaskInstance(flow_id="refund_request", flow_name="退款申请", task_id="t-9")
    state.tasks.paused.append(paused)
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[ResumeTaskCommand(command="resume_task", task_id="t-9")]
        )
    )
    result = validator.validate(plan, state, catalog)
    assert result.valid


def test_cancel_active_task_ok(validator, state, catalog):
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[CancelTaskCommand(command="cancel_task", task_id="t-1")]
        )
    )
    result = validator.validate(plan, state, catalog)
    assert result.valid


def test_knowledge_intent_valid(validator, state, catalog):
    plan = TurnPlan(knowledge=KnowledgeTurnPlan(intents=["refund_policy"]))
    result = validator.validate(plan, state, catalog)
    assert result.valid


def test_knowledge_multiple_intents_ok(validator, state, catalog):
    plan = TurnPlan(knowledge=KnowledgeTurnPlan(intents=["refund_policy", "enroll_policy"]))
    result = validator.validate(plan, state, catalog)
    assert result.valid


def test_knowledge_unknown_intent_rejected(validator, state, catalog):
    plan = TurnPlan(knowledge=KnowledgeTurnPlan(intents=["no_such_intent"]))
    result = validator.validate(plan, state, catalog)
    assert not result.valid
    assert result.reason == ClarifyReason.UNKNOWN_KNOWLEDGE_INTENT


def test_chitchat_ok(validator, state, catalog):
    plan = TurnPlan(chitchat=ChitchatTurnPlan())
    result = validator.validate(plan, state, catalog)
    assert result.valid


def test_object_message_requires_active_task(catalog):
    empty_state = DialogueState(sender_id="1001")
    msg = UserMessage(
        sender_id="1001",
        message_id="m2",
        session_id="s1",
        type="object",
        object=None,
    )
    from edu_assistant.domain.message import MessageObject, MessageType

    msg.type = MessageType.OBJECT
    msg.object = MessageObject(id="ORD20240401005", type="order")
    result = validate_object_message(empty_state, msg)
    assert not result.valid
    assert result.reason == ClarifyReason.OBJECT_REQUIRES_INTENT


def test_object_type_whitelist():
    from edu_assistant.domain.message import MessageObject, MessageType, UserMessage

    msg = UserMessage(
        sender_id="1001",
        message_id="m3",
        session_id="s1",
        type=MessageType.OBJECT,
        object=MessageObject(id="1", type="product"),
    )
    result = validate_object_message(DialogueState(sender_id="1001"), msg)
    assert not result.valid
    assert result.reason == ClarifyReason.OBJECT_TYPE_MISMATCH


# —— Command.from_dict 对 LLM 输出的容错 ——

def test_from_dict_tolerates_extra_keys():
    """命令名合法但带 LLM 附带的多余字段时，应取声明字段而非整体作废。"""
    cmd = Command.from_dict(
        {"command": "start_flow", "flow": "order_status_query", "flow_name": "订单状态查询", "reason": "用户要查订单"}
    )
    assert isinstance(cmd, StartFlowCommand)
    assert cmd.command == "start_flow"
    assert cmd.flow == "order_status_query"


def test_from_dict_rejects_unknown_command_name():
    cmd = Command.from_dict({"command": "do_magic", "flow": "x"})
    assert type(cmd).__name__ == "UnknownCommand"


def test_set_slots_targets_start_flow_not_active_task(validator, state, catalog):
    """start_flow + set_slots：槽位应对新流程校验，而非当前活跃任务（E2E 发现的缺陷）。"""
    # 活跃任务改为退款（等订单号），不含 cohort_name 槽位
    state.tasks.active = TaskInstance(
        flow_id="refund_request", flow_name="退款申请", step_id="ask_order_no", slots={}
    )
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[
                StartFlowCommand(command="start_flow", flow="learning_progress_query"),
                SetSlotsCommand(command="set_slots", slots={"cohort_name": "考研数学冲刺模考班"}),
            ]
        )
    )
    result = validator.validate(plan, state, catalog)
    assert result.valid, f"应通过: {result.reason}"


def test_set_slots_top_level_declared_slot_allowed(validator, state, catalog):
    """顶层声明过的槽位即使不属于当前流程的 collect 槽，也允许 set_slots
    （供查询流程的可选过滤槽等场景；跨流程仅软限制）。"""
    state.tasks.active = TaskInstance(
        flow_id="refund_request", flow_name="退款申请", step_id="ask_order_no", slots={}
    )
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[SetSlotsCommand(command="set_slots", slots={"ticket_no": "TK123"})]
        )
    )
    result = validator.validate(plan, state, catalog)
    assert result.valid, f"应通过: {result.reason}"


def test_set_slots_fully_undeclared_slot_rejected(validator, state, catalog):
    """顶层也没声明的槽位名 → 拒绝（防 LLM 编造槽位）。"""
    state.tasks.active = TaskInstance(
        flow_id="refund_request", flow_name="退款申请", step_id="ask_order_no", slots={}
    )
    plan = TurnPlan(
        task=TaskTurnPlan(
            commands=[SetSlotsCommand(command="set_slots", slots={"made_up_slot": "x"})]
        )
    )
    result = validator.validate(plan, state, catalog)
    assert not result.valid and result.reason == ClarifyReason.INVALID_TASK_COMMAND
