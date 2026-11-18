"""流程加载与启动期校验测试。"""

from __future__ import annotations

import textwrap

import pytest

from edu_assistant.task.action.registry import registered_action_names
from edu_assistant.task.flow.loader import FlowLoader
from edu_assistant.utils.errors import FlowDefinitionError

# 导入 edu action 模块触发注册
import edu_assistant.task.action.edu.order_actions  # noqa: F401
import edu_assistant.task.action.edu.progress_actions  # noqa: F401
import edu_assistant.task.action.edu.refund_actions  # noqa: F401
import edu_assistant.task.action.edu.ticket_actions  # noqa: F401


def write_yaml(tmp_path, content: str):
    path = tmp_path / "flows.yml"
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


MINIMAL_VALID = """
    slots:
      order_no: {type: text, label: 订单号}
    flows:
      order_status_query:
        name: 订单状态查询
        description: 查订单
        steps:
          - {id: start, type: start, next: ask}
          - {id: ask, type: collect, slot_name: order_no, template: {text: "请提供订单号"}, next: resolve}
          - {id: resolve, type: action, action: action_resolve_order, next: show}
          - {id: show, type: response, template: {text: "{{ slots.order_summary }}"}, next: end}
          - {id: end, type: end, next: []}
"""


def test_load_minimal_valid(tmp_path):
    path = write_yaml(tmp_path, MINIMAL_VALID)
    catalog = FlowLoader().load(path, registered_action_names())
    assert "order_status_query" in catalog.flows
    flow = catalog.flows["order_status_query"]
    assert flow.start_step_id == "start"
    assert "order_no" in flow.slots


def test_real_config_loads(tmp_path):
    """真实流程配置可加载且校验通过。"""
    from edu_assistant.conf.config import get_settings

    catalog = FlowLoader().load(get_settings().flow_config_path, registered_action_names())
    assert set(catalog.flows) == {
        "order_status_query",
        "learning_progress_query",
        "refund_request",
        "ticket_submit",
        "refund_status_query",
        "ticket_status_query",
    }


def test_unknown_slot_rejected(tmp_path):
    bad = MINIMAL_VALID.replace("slot_name: order_no", "slot_name: not_declared")
    with pytest.raises(FlowDefinitionError):
        FlowLoader().load(write_yaml(tmp_path, bad), registered_action_names())


def test_unknown_action_rejected(tmp_path):
    bad = MINIMAL_VALID.replace("action: action_resolve_order", "action: action_missing")
    with pytest.raises(FlowDefinitionError):
        FlowLoader().load(write_yaml(tmp_path, bad), registered_action_names())


def test_missing_next_target_rejected(tmp_path):
    bad = MINIMAL_VALID.replace("next: resolve", "next: nonexistent_step")
    with pytest.raises(FlowDefinitionError):
        FlowLoader().load(write_yaml(tmp_path, bad), registered_action_names())


def test_duplicate_step_id_rejected(tmp_path):
    bad = MINIMAL_VALID.replace("id: show, type: response", "id: resolve, type: response")
    with pytest.raises(FlowDefinitionError):
        FlowLoader().load(write_yaml(tmp_path, bad), registered_action_names())


def test_two_starts_rejected(tmp_path):
    bad = MINIMAL_VALID.replace(
        "- {id: show, type: response",
        "- {id: start2, type: start, next: end}\n        - {id: show, type: response",
    )
    with pytest.raises(FlowDefinitionError):
        FlowLoader().load(write_yaml(tmp_path, bad), registered_action_names())


def test_enum_slot_requires_choices(tmp_path):
    bad = MINIMAL_VALID.replace(
        "slots:",
        "slots:\n      refund_type: {type: enum, label: 类型}",
        1,
    )
    with pytest.raises(FlowDefinitionError):
        FlowLoader().load(write_yaml(tmp_path, bad), registered_action_names())


def test_bad_condition_rejected(tmp_path):
    bad = MINIMAL_VALID.replace(
        "next: show", 'next:\n            - {if: "__import__(\'os\')", then: show}\n            - {else: end}'
    )
    with pytest.raises(FlowDefinitionError):
        FlowLoader().load(write_yaml(tmp_path, bad), registered_action_names())
