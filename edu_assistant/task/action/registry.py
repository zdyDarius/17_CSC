"""Action 注册表：新增业务能力时新增 Action，不修改通用执行器。"""

from __future__ import annotations

from edu_assistant.task.action.base import Action
from edu_assistant.utils.errors import FlowDefinitionError

_REGISTRY: dict[str, Action] = {}


def register_action(name: str):
    def decorator(cls):
        cls.name = name
        _REGISTRY[name] = cls()
        return cls

    return decorator


def get_action(name: str) -> Action:
    if name not in _REGISTRY:
        raise FlowDefinitionError(f"未注册的 action: {name}")
    return _REGISTRY[name]


def registered_action_names() -> set[str]:
    return set(_REGISTRY.keys())
