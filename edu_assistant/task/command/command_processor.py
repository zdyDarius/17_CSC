"""命令处理器：命令只改任务状态，不直接写业务数据。"""

from __future__ import annotations

import uuid

from edu_assistant.domain.state import DialogueState, TaskInstance
from edu_assistant.lifecycle.models import TaskEvent
from edu_assistant.plan.command import (
    CancelTaskCommand,
    Command,
    ResumeTaskCommand,
    SetSlotsCommand,
    StartFlowCommand,
)
from edu_assistant.task.flow.models import FlowCatalog
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


class CommandProcessor:
    def __init__(self, max_paused: int = 5) -> None:
        self.max_paused = max_paused

    async def process_commands(
        self,
        commands: list[Command],
        state: DialogueState,
        catalog: FlowCatalog,
    ) -> list[TaskEvent]:
        events: list[TaskEvent] = []
        for command in commands:
            event = self._execute(command, state, catalog)
            if event is not None:
                events.append(event)
        return events

    def _execute(
        self,
        command: Command,
        state: DialogueState,
        catalog: FlowCatalog,
    ) -> TaskEvent | None:
        if isinstance(command, StartFlowCommand):
            flow = catalog.flows.get(command.flow)
            if flow is None:
                logger.warning("start_flow 引用未知流程: %s", command.flow)
                return None
            # 任务切换：当前有活跃任务时自动压栈（4.3 节语义）
            if state.tasks.active is not None and len(state.tasks.paused) >= self.max_paused:
                logger.warning("暂停栈已满，拒绝 start_flow")
                return None
            task = TaskInstance(
                flow_id=flow.id,
                flow_name=flow.name,
                step_id=flow.start_step_id,
                task_id=str(uuid.uuid4()),
            )
            return state.tasks.start_task(task, self.max_paused)

        if isinstance(command, SetSlotsCommand):
            active = state.tasks.active
            if active is None:
                logger.warning("set_slots 无活跃任务，忽略")
                return None
            if isinstance(command.slots, dict):
                active.slots.update(command.slots)
            return None

        if isinstance(command, ResumeTaskCommand):
            if not command.task_id:
                return None
            return state.tasks.resume_task(command.task_id)

        if isinstance(command, CancelTaskCommand):
            if not command.task_id:
                return None
            return state.tasks.cancel_task(command.task_id)

        logger.warning("未知命令类型: %r", command)
        return None
