"""流程执行器：状态机推进，支持任务切换/恢复链，有明确死循环异常。"""

from __future__ import annotations

from edu_assistant.domain.message import BotMessage, UserMessage
from edu_assistant.domain.state import DialogueState, TaskInstance
from edu_assistant.lifecycle.models import TaskEvent, event_prompt
from edu_assistant.task.action.action_runner import ActionRunner
from edu_assistant.task.action.base import ActionContext, ActionResult
from edu_assistant.task.command.command_processor import CommandProcessor
from edu_assistant.task.flow.models import FlowCatalog
from edu_assistant.task.flow.steps import (
    ActionFlowStep,
    CollectSlotStep,
    ConditionalLink,
    EndFlowStep,
    FlowStepLink,
    ResponseFlowStep,
    StartFlowStep,
    StaticLink,
)
from edu_assistant.task.response.response_render import ResponseRender
from edu_assistant.utils.errors import FlowInfiniteLoopError
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

# 业务对象消息 → 槽位映射（项目计划 4.3 白名单）
OBJECT_SLOT_MAP = {
    "order": "order_no",
    "series": "series_name",
    "cohort": "cohort_name",
    "refund_request": "refund_request_id",
}

# collect 追问前附加的一次性提示槽位
INVALID_KEYS = (
    "order_item_choice_invalid",
    "cohort_choice_invalid",
    "refund_type_invalid",
    "ticket_type_invalid",
)


class FlowExecutor:
    def __init__(
        self,
        response_render: ResponseRender,
        action_runner: ActionRunner,
        command_processor: CommandProcessor,
        flow_catalog: FlowCatalog,
        max_steps: int = 30,
    ) -> None:
        self.response_render = response_render
        self.action_runner = action_runner
        self.command_processor = command_processor
        self.flow_catalog = flow_catalog
        self.max_steps = max_steps

    async def execute(
        self,
        state: DialogueState,
        user_message: UserMessage,
        ctx_factory,
    ) -> tuple[list[BotMessage], list[TaskEvent]]:
        """推进活跃任务；一轮内允许任务切换/恢复链，步数超限抛死循环异常。"""
        messages: list[BotMessage] = []
        events: list[TaskEvent] = []
        step_counts: dict[tuple[str, str], int] = {}

        for _ in range(self.max_steps):
            active = state.tasks.active
            if active is None:
                return messages, events
            flow = self.flow_catalog.get_flow(active.flow_id)
            step = flow.get_step(active.step_id)

            # 防死循环：同一任务同一步骤重复执行超过 3 次即异常
            key = (active.task_id, str(active.step_id))
            step_counts[key] = step_counts.get(key, 0) + 1
            if step_counts[key] > 3:
                raise FlowInfiniteLoopError(flow.id)

            if isinstance(step, StartFlowStep):
                active.step_id = self._next_step(step.next, active)
                continue

            if isinstance(step, CollectSlotStep):
                if self._try_fill_slot(step, active, state):
                    active.step_id = self._next_step(step.next, active)
                    continue
                # 等待用户输入：渲染追问（附加 invalid 提示）
                messages.append(self._render_collect(step, active, state, user_message))
                return messages, events

            if isinstance(step, ActionFlowStep):
                ctx = ctx_factory(state, user_message)
                result = await self.action_runner.run(step.action, ctx)
                events.extend(
                    await self._apply_result(result, active, state, user_message)
                )
                if state.tasks.active is not active:
                    continue  # 任务已切换（auto_commands），循环处理新任务
                active.step_id = self._next_step(step.next, active)
                continue

            if isinstance(step, ResponseFlowStep):
                messages.append(
                    self.response_render.render(
                        step.template.get("text", ""), state, user_message
                    )
                )
                active.step_id = self._next_step(step.next, active)
                continue

            if isinstance(step, EndFlowStep):
                event = state.tasks.complete_active()
                if event is not None:
                    events.append(event)
                # 4.3 恢复语义：新任务正常结束后自动弹出栈顶并提示继续。
                # 栈顶若为等待退款的工单任务，回到重检步骤并带回退款申请。
                resumed_event = self._auto_resume_top(state)
                if resumed_event is not None:
                    events.append(resumed_event)
                    prompt = event_prompt(resumed_event, self.flow_catalog.flow_names())
                    if prompt:
                        messages.append(BotMessage(text=prompt))
                continue

        raise FlowInfiniteLoopError(
            state.tasks.active.flow_id if state.tasks.active else "unknown"
        )

    # —— 内部 ——

    # 恢复写操作流程时清空的确认槽位：确认步骤将重新展示关键对象（4.3）
    CONFIRM_SLOTS = ("confirm_refund", "confirm_ticket", "order_item_choice")

    def _auto_resume_top(self, state: DialogueState) -> TaskEvent | None:
        """新任务结束后自动恢复栈顶暂停任务（4.3 恢复语义）。

        - wait_refund_link（退款转工单）：回到 resume_step_id 重检步骤。
        - 写操作流程恢复：清确认类槽位，确认页重新展示明细与金额。
        """
        if not state.tasks.paused:
            return None
        top = state.tasks.paused[-1]
        event = state.tasks.resume_task(top.task_id)
        if event is None:
            return None
        active = state.tasks.active
        if active is None:
            return event
        if active.wait_refund_link and active.resume_step_id:
            active.step_id = active.resume_step_id
            return event
        # 普通恢复：写操作流程清确认槽位，确认步骤重新展示关键对象
        for key in self.CONFIRM_SLOTS:
            active.slots.pop(key, None)
        return event

    async def _apply_result(
        self,
        result: ActionResult,
        active: TaskInstance,
        state: DialogueState,
        user_message: UserMessage,
    ) -> list[TaskEvent]:
        """应用 Action 结果：清槽位 → 更新槽位 → 执行自动命令（可能切换任务）。"""
        events: list[TaskEvent] = []
        for name in result.clear_slots:
            active.slots.pop(name, None)
        if result.slots_update:
            active.slots.update(result.slots_update)
        if result.resume_step_id:
            active.resume_step_id = result.resume_step_id
        if result.auto_commands:
            events.extend(
                await self.command_processor.process_commands(
                    result.auto_commands, state, self.flow_catalog
                )
            )
        return events

    def _try_fill_slot(
        self, step: CollectSlotStep, active: TaskInstance, state: DialogueState
    ) -> bool:
        """槽位已有值 → 通过；focused_object 匹配当前槽位 → 补槽。"""
        value = active.slots.get(step.slot_name)
        if value not in (None, "", [], {}):
            return True
        focused = state.focused_object
        if focused is not None:
            slot = OBJECT_SLOT_MAP.get(focused.type)
            if slot == step.slot_name:
                active.slots[slot] = focused.id
                return True
        return False

    def _render_collect(
        self,
        step: CollectSlotStep,
        active: TaskInstance,
        state: DialogueState,
        user_message: UserMessage,
    ) -> BotMessage:
        """渲染追问模板，附加 invalid 提示（一次性）。"""
        prefix = ""
        for key in INVALID_KEYS:
            if active.slots.get(key):
                prefix = f"{active.slots.pop(key)}\n"
                break
        message = self.response_render.render(
            step.template.get("text", ""), state, user_message
        )
        message.text = prefix + message.text
        return message

    def _next_step(self, links: list[FlowStepLink], active: TaskInstance) -> str:
        if not links:
            raise FlowInfiniteLoopError(active.flow_id)
        if len(links) == 1:
            return links[0].target
        for link in links:
            if isinstance(link, ConditionalLink):
                try:
                    if link.compiled().evaluate(active.slots):
                        return link.target
                except Exception:  # noqa: BLE001
                    logger.exception("条件求值失败: %s", link.condition)
                continue
            if isinstance(link, StaticLink):
                return link.target
        return links[-1].target
