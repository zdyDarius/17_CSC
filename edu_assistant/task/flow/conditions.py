"""安全条件解析：白名单 AST 求值，禁止 eval。

支持的受限表达式（示例）：
- slots.order_item_id                  槽位存在且非空
- not slots.order_no                   槽位缺失或为空
- slots.refund_type == "personal_reason"
- slots.order_item_count > 1
- slots.a and not slots.b

语法白名单：Name(slots)、属性访问、下标访问、Compare(==,!=,<,<=,>,>=)、
BoolOp(and/or/not)、UnaryOp(not)、常量(str/int/float/bool/None)。
其他任何语法在加载期即抛 FlowDefinitionError。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from edu_assistant.utils.errors import FlowDefinitionError


class _Compiler(ast.NodeVisitor):
    """把受限 AST 编译成闭包树。"""

    def __init__(self, source: str) -> None:
        self.source = source

    def compile_expr(self) -> "Condition":
        try:
            tree = ast.parse(self.source, mode="eval")
        except SyntaxError as exc:
            raise FlowDefinitionError(f"条件表达式语法错误: {self.source!r} ({exc})")
        fn = self.visit(tree.body)
        return Condition(source=self.source, check=fn)

    # —— 表达式节点 ——

    def visit_BoolOp(self, node: ast.BoolOp) -> object:
        values = [self.visit(v) for v in node.values]
        if isinstance(node.op, ast.And):
            return lambda slots: all(v(slots) for v in values)
        if isinstance(node.op, ast.Or):
            return lambda slots: any(v(slots) for v in values)
        raise FlowDefinitionError(f"不支持的布尔运算: {self.source!r}")

    def visit_UnaryOp(self, node: ast.UnaryOp) -> object:
        if not isinstance(node.op, ast.Not):
            raise FlowDefinitionError(f"不支持的一元运算: {self.source!r}")
        operand = self.visit(node.operand)
        return lambda slots: not operand(slots)

    def visit_Compare(self, node: ast.Compare) -> object:
        left = self.visit(node.left)
        comparators = [self.visit(c) for c in node.comparators]
        ops: list[type] = []
        for op in node.ops:
            if not isinstance(op, (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                raise FlowDefinitionError(f"不支持的比较运算: {self.source!r}")
            ops.append(type(op))
        eq_table: dict[type, object] = {
            ast.Eq: lambda a, b: a == b,
            ast.NotEq: lambda a, b: a != b,
            ast.Lt: lambda a, b: _safe_lt(a, b),
            ast.LtE: lambda a, b: _safe_le(a, b),
            ast.Gt: lambda a, b: _safe_lt(b, a),
            ast.GtE: lambda a, b: _safe_le(b, a),
        }

        def run(slots: dict) -> bool:
            value = left(slots)
            for op, comp in zip(ops, comparators):
                value = eq_table[op](value, comp(slots))  # type: ignore[operator]
            return bool(value)

        return run

    def visit_Attribute(self, node: ast.Attribute) -> object:
        if not isinstance(node.ctx, ast.Load):
            raise FlowDefinitionError(f"不支持的表达式: {self.source!r}")
        owner = self.visit(node.value)
        attr = node.attr
        if attr.startswith("_"):
            raise FlowDefinitionError(f"不允许访问私有属性: {self.source!r}")
        return lambda slots: _truthy(_get(owner(slots), attr))

    def visit_Subscript(self, node: ast.Subscript) -> object:
        if not isinstance(node.ctx, ast.Load):
            raise FlowDefinitionError(f"不支持的表达式: {self.source!r}")
        owner = self.visit(node.value)
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            key_fn = lambda slots, k=key.value: k  # noqa: E731
        elif isinstance(key, ast.Constant) and isinstance(key.value, int):
            key_fn = lambda slots, k=key.value: k  # noqa: E731
        else:
            raise FlowDefinitionError(f"下标只允许常量: {self.source!r}")
        return lambda slots: _truthy(_get(owner(slots), key_fn(slots)))

    def visit_Name(self, node: ast.Name) -> object:
        if node.id != "slots":
            raise FlowDefinitionError(
                f"表达式中只允许 slots 变量: {self.source!r}"
            )
        return lambda slots: _SlotsView(slots)

    def visit_Constant(self, node: ast.Constant) -> object:
        value = node.value
        return lambda slots: value

    def generic_visit(self, node: ast.AST) -> object:
        raise FlowDefinitionError(
            f"条件表达式包含不允许的语法 {type(node).__name__}: {self.source!r}"
        )


class _SlotsView:
    """slots 的只读视图：属性/下标访问缺失时返回 None。"""

    def __init__(self, slots: dict) -> None:
        object.__setattr__(self, "_slots", slots or {})

    def __getattr__(self, name: str) -> object:
        if name.startswith("_"):
            raise AttributeError(name)
        return _truthy(self._slots.get(name))  # type: ignore[attr-defined]

    def __getitem__(self, key: str) -> object:
        return _truthy(self._slots.get(key))  # type: ignore[attr-defined]


def _get(owner: object, key: object) -> object:
    if isinstance(owner, _SlotsView):
        return owner[key]  # type: ignore[index]
    if isinstance(owner, dict):
        return _truthy(owner.get(key))
    return None


def _truthy(value: object) -> object:
    """空列表/空 dict/空串 归一为 None，避免误判为真。"""
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return None
    return value


def _safe_lt(a: object, b: object) -> bool:
    try:
        return bool(a < b)  # type: ignore[operator]
    except TypeError:
        return False


def _safe_le(a: object, b: object) -> bool:
    try:
        return bool(a <= b)  # type: ignore[operator]
    except TypeError:
        return False


@dataclass
class Condition:
    """编译后的条件表达式。"""

    source: str
    check: object

    def evaluate(self, slots: dict) -> bool:
        try:
            return bool(self.check(slots))  # type: ignore[call-arg]
        except Exception:
            return False


def parse_condition(source: str) -> Condition:
    return _Compiler(source).compile_expr()
