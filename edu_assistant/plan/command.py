"""任务命令模型：LLM 只生成结构化计划，不直接改状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Command:
    command: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Command":
        name = data.get("command")
        clz = COMMAND_NAME_TO_CLASS.get(name) if isinstance(name, str) else None
        if clz is None:
            return UnknownCommand(command=str(name or ""))
        payload = {k: v for k, v in data.items() if k != "command"}
        try:
            return clz(**payload)
        except TypeError:
            return UnknownCommand(command=str(name))


@dataclass
class StartFlowCommand(Command):
    flow: str = ""


@dataclass
class SetSlotsCommand(Command):
    slots: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResumeTaskCommand(Command):
    task_id: str = ""


@dataclass
class CancelTaskCommand(Command):
    task_id: str = ""


@dataclass
class UnknownCommand(Command):
    """无法识别的命令（校验器拒绝）。"""


COMMAND_NAME_TO_CLASS: dict[str, type[Command]] = {
    "start_flow": StartFlowCommand,
    "set_slots": SetSlotsCommand,
    "resume_task": ResumeTaskCommand,
    "cancel_task": CancelTaskCommand,
}
