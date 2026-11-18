"""对话引擎：一轮消息 → 规划 → 校验 → 分流（task/knowledge/chitchat/clarify）→ 回复。

与参考项目相比的修复：
- 流程目录启动时加载一次（不在每轮请求中重复读盘）
- 对象消息：白名单类型 + 当前步骤声明的槽位匹配（含判空），不强行补槽
- 规划失败（LLM 非法 JSON）进入可控澄清，不产生 500
"""

from __future__ import annotations

from dataclasses import dataclass, field

from edu_assistant.clarify.clarify_response import ClarifyResponse
from edu_assistant.chitchat.chit_chat import ChitChatHandler
from edu_assistant.domain.message import BotMessage, UserMessage
from edu_assistant.domain.state import DialogueState
from edu_assistant.knowledge.knowledge_handler import KnowledgeHandler
from edu_assistant.lifecycle.models import TaskEvent
from edu_assistant.plan.command import SetSlotsCommand
from edu_assistant.plan.turn_plan_validator import (
    TurnPlanValidator,
    validate_object_message,
)
from edu_assistant.plan.turn_planner import PlanError, TurnPlanner
from edu_assistant.prompts.models import ClarifyReason, TurnPlan
from edu_assistant.task.action.base import ActionContext
from edu_assistant.task.flowexecutor.flow_executor import OBJECT_SLOT_MAP
from edu_assistant.task.flow.models import FlowCatalog
from edu_assistant.task.flow.steps import CollectSlotStep
from edu_assistant.task.task_handler import TaskHandler
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class TurnOutcome:
    """一轮处理结果 + 轨迹信息（供 service 记 trace / task_event）。"""

    messages: list[BotMessage] = field(default_factory=list)
    track: str = "chitchat"  # task / knowledge / chitchat / clarify
    plan_summary: str = ""
    actions: list[str] = field(default_factory=list)
    events: list[TaskEvent] = field(default_factory=list)  # 任务生命周期事件（审计）


class DialogueEngine:
    def __init__(
        self,
        turn_planner: TurnPlanner,
        turn_plan_validator: TurnPlanValidator,
        task_handler: TaskHandler,
        knowledge_handler: KnowledgeHandler,
        chit_chat: ChitChatHandler,
        clarify_response: ClarifyResponse,
        flow_catalog: FlowCatalog,
        action_deps,
    ) -> None:
        self.turn_planner = turn_planner
        self.turn_plan_validator = turn_plan_validator
        self.task_handler = task_handler
        self.knowledge_handler = knowledge_handler
        self.chit_chat = chit_chat
        self.clarify_response = clarify_response
        self.flow_catalog = flow_catalog
        self.action_deps = action_deps  # ActionRunner 依赖（settings/edu_client/adapter）

    def _ctx_factory(self, state: DialogueState, user_message: UserMessage) -> ActionContext:
        return ActionContext(
            state=state,
            user_message=user_message,
            settings=self.action_deps.settings,
            edu_client=self.action_deps.edu_client,
            order_item_adapter=self.action_deps.order_item_adapter,
        )

    async def process_message(
        self, state: DialogueState, user_message: UserMessage
    ) -> TurnOutcome:
        if user_message.type == "object":
            return await self._execute_object_message(state, user_message)
        return await self._execute_text_message(state, user_message)

    # —— 文本消息 ——

    async def _execute_text_message(
        self, state: DialogueState, user_message: UserMessage
    ) -> TurnOutcome:
        # 1 规划
        try:
            turn_plan: TurnPlan = await self.turn_planner.plan(
                state, user_message, self.flow_catalog
            )
        except PlanError as exc:
            logger.warning("规划失败: %s", exc)
            messages = await self.clarify_response.response(
                ClarifyReason.PLAN_FAILED, state, user_message
            )
            return TurnOutcome(messages=messages, track="clarify", plan_summary="plan_error")

        # 2 严格校验
        validation = self.turn_plan_validator.validate(turn_plan, state, self.flow_catalog, user_message)
        if not validation.valid:
            if validation.reason == ClarifyReason.MULTIPLE_TRACKS:
                # 确定性仲裁（与规划 prompt 的优先级规则一致，消除多轨抖动死胡同）：
                # - 任务进行中：知识插问优先，任务保留（计划 4.3）
                # - 无进行中任务：存在可执行的 task 计划时业务流程优先（prompt 判断顺序 2）
                if turn_plan.knowledge is not None and state.tasks.active is not None:
                    messages = await self.knowledge_handler.handle(
                        intents=turn_plan.knowledge.intents,
                        state=state,
                        user_message=user_message,
                    )
                    return TurnOutcome(
                        messages=messages,
                        track="knowledge",
                        plan_summary=_plan_summary(turn_plan) + " → 插问仲裁",
                        actions=turn_plan.knowledge.intents,
                    )
                if turn_plan.task is not None and turn_plan.task.commands:
                    messages = await self.task_handler.handle(
                        commands=turn_plan.task.commands,
                        state=state,
                        user_message=user_message,
                        ctx_factory=self._ctx_factory,
                    )
                    return TurnOutcome(
                        messages=messages,
                        track="task",
                        plan_summary=_plan_summary(turn_plan) + " → 任务优先仲裁",
                        actions=[c.command for c in turn_plan.task.commands],
                    )
            logger.info("计划校验失败: %s %s", validation.reason, validation.detail)
            messages = await self.clarify_response.response(
                validation.reason or ClarifyReason.AMBIGUOUS_INPUT, state, user_message
            )
            return TurnOutcome(
                messages=messages,
                track="clarify",
                plan_summary=_plan_summary(turn_plan),
            )

        # 3 分流
        if turn_plan.task is not None:
            messages, events = await self.task_handler.handle(
                commands=turn_plan.task.commands,
                state=state,
                user_message=user_message,
                ctx_factory=self._ctx_factory,
            )
            return TurnOutcome(
                messages=messages,
                track="task",
                plan_summary=_plan_summary(turn_plan),
                actions=[c.command for c in turn_plan.task.commands],
                events=events,
            )

        if turn_plan.knowledge is not None:
            messages = await self.knowledge_handler.handle(
                intents=turn_plan.knowledge.intents,
                state=state,
                user_message=user_message,
            )
            return TurnOutcome(
                messages=messages,
                track="knowledge",
                plan_summary=_plan_summary(turn_plan),
                actions=turn_plan.knowledge.intents,
            )

        messages = await self.chit_chat.chit_chat(state, user_message)
        return TurnOutcome(messages=messages, track="chitchat", plan_summary="chitchat")

    # —— 对象消息 ——

    async def _execute_object_message(
        self, state: DialogueState, user_message: UserMessage
    ) -> TurnOutcome:
        obj = user_message.object
        if obj is not None:
            # 白名单类型已在 API 层校验；这里聚焦为上下文（后续步骤可匹配槽位）
            state.focused_object = obj

        validation = validate_object_message(state, user_message)
        if not validation.valid:
            reason = validation.reason or ClarifyReason.OBJECT_TYPE_MISMATCH
            if reason == ClarifyReason.OBJECT_REQUIRES_INTENT:
                # 当前无进行中任务：说明对象已记录，引导用户表达意图
                type_label = _OBJECT_LABELS.get(obj.type, obj.type) if obj else "业务"
                messages = [
                    BotMessage(
                        text=(
                            f"收到你发送的{type_label}对象"
                            f"（{obj.title or obj.id}）。请告诉我你想做什么，例如："
                            "查询订单状态、申请退款、提交工单，或告诉我你想咨询的课程/班次。"
                        )
                    )
                ]
                return TurnOutcome(messages=messages, track="clarify", plan_summary="object_no_task")
            messages = await self.clarify_response.response(reason, state, user_message)
            return TurnOutcome(messages=messages, track="clarify", plan_summary="object_invalid")

        # 当前步骤是 collect 且槽位与对象类型匹配 → 补槽推进
        slot_name = self._matching_slot(state)
        if slot_name is None:
            messages = await self.clarify_response.response(
                ClarifyReason.OBJECT_TYPE_MISMATCH, state, user_message
            )
            return TurnOutcome(messages=messages, track="clarify", plan_summary="object_slot_mismatch")

        command = SetSlotsCommand(command="set_slots", slots={slot_name: obj.id if obj else ""})
        messages, events = await self.task_handler.handle(
            commands=[command], state=state, user_message=user_message, ctx_factory=self._ctx_factory
        )
        return TurnOutcome(
            messages=messages,
            track="task",
            plan_summary="object_fill_slot",
            actions=["set_slots"],
            events=events,
        )

    def _matching_slot(self, state: DialogueState) -> str | None:
        """当前 active 步骤是 collect 且其槽位接受该对象类型 → 返回槽位名。"""
        active = state.tasks.active
        if active is None or state.focused_object is None:
            return None
        flow = self.flow_catalog.flows.get(active.flow_id)
        if flow is None:
            return None
        step = flow.steps.get(active.step_id or "")
        if not isinstance(step, CollectSlotStep):
            return None
        expected = OBJECT_SLOT_MAP.get(state.focused_object.type)
        if expected == step.slot_name:
            return step.slot_name
        return None


_OBJECT_LABELS = {
    "order": "订单",
    "series": "课程",
    "cohort": "班次",
    "refund_request": "退款申请",
}


def _plan_summary(turn_plan: TurnPlan) -> str:
    if turn_plan.task is not None:
        commands = [
            getattr(c, "flow", None) or getattr(c, "task_id", None) or c.command
            for c in turn_plan.task.commands
        ]
        return "task: " + ",".join(str(x) for x in commands)
    if turn_plan.knowledge is not None:
        return "knowledge: " + ",".join(turn_plan.knowledge.intents)
    return "chitchat"
