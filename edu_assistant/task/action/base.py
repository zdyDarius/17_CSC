"""Action 基础：上下文、调用、结果。

- Action 是业务能力的隔离单元：调用 edu-data API 与只读适配器，把结果写入槽位。
- Action 不修改流程结构（压栈/恢复由 CommandProcessor/end 步骤处理）。
- ActionResult 统一：槽位更新、槽位清理（重问）、自动命令（退款转工单切换）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from edu_assistant.conf.config import Settings
from edu_assistant.domain.message import UserMessage
from edu_assistant.domain.state import DialogueState
from edu_assistant.integrations.edu_data.client import EduDataClient
from edu_assistant.integrations.edu_data.order_item_adapter import OrderItemAdapter
from edu_assistant.plan.command import Command


@dataclass
class ActionContext:
    state: DialogueState
    user_message: UserMessage
    settings: Settings
    edu_client: EduDataClient
    order_item_adapter: OrderItemAdapter

    @property
    def user_id(self) -> str:
        return self.state.sender_id

    @property
    def slots(self) -> dict:
        if self.state.tasks.active is None:
            return {}
        return self.state.tasks.active.slots


@dataclass
class ActionResult:
    slots_update: dict[str, Any] = field(default_factory=dict)
    clear_slots: list[str] = field(default_factory=list)
    auto_commands: list[Command] = field(default_factory=list)
    # 退款转工单：当前任务被暂停后恢复时的重检步骤 ID
    resume_step_id: str = ""


class Action(Protocol):
    name: str

    async def run(self, ctx: ActionContext) -> ActionResult: ...
