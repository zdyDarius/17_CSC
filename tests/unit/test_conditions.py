"""条件白名单求值测试（禁止 eval）。"""

from __future__ import annotations

import pytest

from edu_assistant.task.flow.conditions import parse_condition
from edu_assistant.utils.errors import FlowDefinitionError


def test_slot_exists():
    cond = parse_condition("slots.order_item_id")
    assert cond.evaluate({"order_item_id": 5}) is True
    assert cond.evaluate({"order_item_id": None}) is False
    assert cond.evaluate({}) is False


def test_empty_collection_is_falsy():
    cond = parse_condition("slots.order_items")
    assert cond.evaluate({"order_items": []}) is False
    assert cond.evaluate({"order_items": [1, 2]}) is True
    assert cond.evaluate({"order_items": {}}) is False


def test_not_expression():
    cond = parse_condition("not slots.refund_request_id")
    assert cond.evaluate({}) is True
    assert cond.evaluate({"refund_request_id": 9}) is False


def test_equality():
    cond = parse_condition('slots.ticket_type == "refund"')
    assert cond.evaluate({"ticket_type": "refund"}) is True
    assert cond.evaluate({"ticket_type": "complaint"}) is False


def test_numeric_compare():
    cond = parse_condition("slots.order_item_count > 1")
    assert cond.evaluate({"order_item_count": 2}) is True
    assert cond.evaluate({"order_item_count": 1}) is False


def test_and_expression():
    cond = parse_condition('slots.ticket_type == "refund" and not slots.refund_request_id')
    assert cond.evaluate({"ticket_type": "refund"}) is True
    assert cond.evaluate({"ticket_type": "refund", "refund_request_id": 3}) is False
    assert cond.evaluate({"ticket_type": "after_sales"}) is False


def test_or_expression():
    cond = parse_condition("slots.a or slots.b")
    assert cond.evaluate({"a": 1}) is True
    assert cond.evaluate({"b": 2}) is True
    assert cond.evaluate({}) is False


def test_subscript_access():
    cond = parse_condition("slots['refund_no']")
    assert cond.evaluate({"refund_no": "RF123"}) is True
    assert cond.evaluate({}) is False


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('ls')",
        "open('/etc/passwd')",
        "slots.__class__",
        "exec('x=1')",
        "lambda: 1",
        "[x for x in (1,2)]",
        "slots.a + slots.b",
        "f'{1}'",
    ],
)
def test_dangerous_syntax_rejected(expr):
    with pytest.raises(FlowDefinitionError):
        parse_condition(expr)


def test_unknown_name_rejected():
    with pytest.raises(FlowDefinitionError):
        parse_condition("globals()")
