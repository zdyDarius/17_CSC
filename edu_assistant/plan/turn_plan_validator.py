"""TurnPlan 严格校验器。

逐一校验（项目计划 3.1，非法计划进入澄清，不允许 KeyError/AttributeError 变 500）：
- 轨道组合：恰好一个主轨道；task 与 knowledge/chitchat 互斥；知识轨道内允许多意图
- 流程 ID：start_flow 引用的 flow 必须存在
- 知识意图：必须在意图目录中
- 命令顺序：start_flow 唯一且在前；resume/cancel 依赖目标任务存在；set_slots 需有活跃任务
- 槽位名称/类型：set_slots 的槽位名必须在流程声明中且类型匹配（int 槽位需数字）
- 当前任务与对象类型：对象消息白名单类型匹配
"""

from __future__ import annotations

from edu_assistant.domain.message import OBJECT_TYPES, UserMessage
from edu_assistant.domain.state import DialogueState
from edu_assistant.plan.command import (
    CancelTaskCommand,
    Command,
    ResumeTaskCommand,
    SetSlotsCommand,
    StartFlowCommand,
    UnknownCommand,
)
from edu_assistant.prompts.models import (
    ChitchatTurnPlan,
    ClarifyReason,
    KnowledgeTurnPlan,
    TaskTurnPlan,
    TurnPlan,
    TurnPlanValidationResult,
)
from edu_assistant.task.flow.models import FlowCatalog
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


class TurnPlanValidator:
    def __init__(self, knowledge_intents: dict[str, dict], max_paused: int = 5) -> None:
        self.knowledge_intents = knowledge_intents
        self.max_paused = max_paused

    def validate(
        self,
        turn_plan: TurnPlan,
        state: DialogueState,
        catalog: FlowCatalog,
        user_message: UserMessage | None = None,
    ) -> TurnPlanValidationResult:
        tracks: list[str] = []
        if turn_plan.task is not None:
            tracks.append("task")
        if turn_plan.knowledge is not None:
            tracks.append("knowledge")
        if turn_plan.chitchat is not None:
            tracks.append("chitchat")

        if len(tracks) == 0:
            return TurnPlanValidationResult(False, ClarifyReason.MISSING_TRACK)
        if len(tracks) > 1:
            return TurnPlanValidationResult(False, ClarifyReason.MULTIPLE_TRACKS)

        track = tracks[0]
        if track == "task":
            return self._validate_task(turn_plan.task, state, catalog)
        if track == "knowledge":
            return self._validate_knowledge(turn_plan.knowledge)
        return TurnPlanValidationResult(True)  # chitchat

    # —— 任务轨道 ——

    def _validate_task(
        self, task: TaskTurnPlan, state: DialogueState, catalog: FlowCatalog
    ) -> TurnPlanValidationResult:
        if not task.commands:
            return TurnPlanValidationResult(False, ClarifyReason.MISSING_TASK_COMMANDS)

        has_start = False
        saw_other = False
        for command in task.commands:
            if isinstance(command, UnknownCommand):
                logger.warning("未知命令: %r", command.command)
                return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)
            if isinstance(command, StartFlowCommand):
                if has_start or saw_other:
                    return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)
                has_start = True
                if command.flow not in catalog.flows:
                    return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)
            else:
                saw_other = True

        active = state.tasks.active
        for command in task.commands:
            if isinstance(command, SetSlotsCommand):
                if active is None and not has_start:
                    return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)
                if active is None:
                    # start_flow 之后的 set_slots：目标流程的槽位在命令执行后校验
                    continue
                flow = catalog.flows.get(active.flow_id)
                if flow is None:
                    return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)
                result = self._validate_slots(command, flow)
                if result is not None:
                    return result
            if isinstance(command, ResumeTaskCommand):
                target_ids = {t.task_id for t in state.tasks.paused}
                if command.task_id not in target_ids:
                    return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)
            if isinstance(command, CancelTaskCommand):
                target_ids = {t.task_id for t in state.tasks.paused}
                if active is not None:
                    target_ids.add(active.task_id)
                if command.task_id not in target_ids:
                    return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)

        # start_flow 时暂停栈已满 → 澄清
        if has_start and active is not None:
            if len(state.tasks.paused) + 1 > self.max_paused:
                return TurnPlanValidationResult(False, ClarifyReason.TOO_MANY_PAUSED_TASKS)
        return TurnPlanValidationResult(True)

    def _validate_slots(self, command: SetSlotsCommand, flow) -> TurnPlanValidationResult | None:
        """槽位名称存在且类型匹配；返回 None 表示通过。"""
        if not isinstance(command.slots, dict):
            return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)
        for name, value in command.slots.items():
            slot = flow.slots.get(name)
            if slot is None:
                logger.warning("set_slots 引用未声明槽位: %s", name)
                return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)
            if slot.type == "int":
                try:
                    int(value)
                except (TypeError, ValueError):
                    return TurnPlanValidationResult(False, ClarifyReason.INVALID_TASK_COMMAND)
            if slot.type == "enum" and slot.choices:
                # 用户的原始表述（中文）先写入 raw 槽位由 action 归一，这里不拒绝中文
                pass
        return None

    # —— 知识轨道 ——

    def _validate_knowledge(self, knowledge: KnowledgeTurnPlan) -> TurnPlanValidationResult:
        if not knowledge.intents:
            return TurnPlanValidationResult(False, ClarifyReason.MISSING_KNOWLEDGE_INTENT)
        for intent in knowledge.intents:
            if intent not in self.knowledge_intents:
                return TurnPlanValidationResult(False, ClarifyReason.UNKNOWN_KNOWLEDGE_INTENT)
        return TurnPlanValidationResult(True)


def validate_object_message(state: DialogueState, user_message: UserMessage) -> TurnPlanValidationResult:
    """对象类型消息：类型白名单 + 当前步骤可匹配性。"""
    obj = user_message.object
    if obj is None:
        return TurnPlanValidationResult(False, ClarifyReason.MISSING_FOCUSED_OBJECT)
    if obj.type not in OBJECT_TYPES:
        return TurnPlanValidationResult(False, ClarifyReason.OBJECT_TYPE_MISMATCH)
    active = state.tasks.active
    if active is None:
        return TurnPlanValidationResult(False, ClarifyReason.OBJECT_REQUIRES_INTENT)
    return TurnPlanValidationResult(True)
