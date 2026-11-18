"""任务处理器：命令改状态 → 事件提示 → 流程推进，合并结果与事件返回。"""

from __future__ import annotations

from edu_assistant.domain.message import BotMessage, UserMessage
from edu_assistant.domain.state import DialogueState
from edu_assistant.lifecycle.models import TaskCanceled, TaskEvent, event_prompt
from edu_assistant.plan.command import Command
from edu_assistant.task.command.command_processor import CommandProcessor
from edu_assistant.task.flow.models import FlowCatalog
from edu_assistant.task.flowexecutor.flow_executor import FlowExecutor


class TaskHandler:
    def __init__(
        self,
        command_processor: CommandProcessor,
        flow_catalog: FlowCatalog,
        flow_executor: FlowExecutor,
    ) -> None:
        self.command_processor = command_processor
        self.flow_catalog = flow_catalog
        self.flow_executor = flow_executor

    async def handle(
        self,
        commands: list[Command],
        state: DialogueState,
        user_message: UserMessage,
        ctx_factory,
    ) -> tuple[list[BotMessage], list[TaskEvent]]:
        """返回 (回复消息, 任务事件)；事件由 service 层落 task_event 表审计。"""
        messages: list[BotMessage] = []
        flow_names = self.flow_catalog.flow_names()

        # 1 命令只改任务状态
        events: list[TaskEvent] = await self.command_processor.process_commands(
            commands, state, self.flow_catalog
        )

        # 2 状态变更 → 中文提示（切换/恢复/取消）
        for event in events:
            prompt = event_prompt(event, flow_names)
            if prompt:
                messages.append(BotMessage(text=prompt))

        # 4.3 取消语义：取消后若存在暂停任务，询问是否恢复（不擅自继续写操作）
        if any(isinstance(e, TaskCanceled) for e in events) and state.tasks.paused:
            names = "、".join(f"「{t.flow_name or t.flow_id}」" for t in state.tasks.paused)
            messages.append(
                BotMessage(
                    text=f"你还有暂停中的任务：{names}。需要继续请说「继续」，不需要可以说「取消」对应的任务。"
                )
            )

        # 3 流程推进（含任务切换与自动恢复链）
        flow_messages, flow_events = await self.flow_executor.execute(
            state, user_message, ctx_factory
        )
        messages.extend(flow_messages)
        events.extend(flow_events)
        return messages, events
