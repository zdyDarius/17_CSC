"""TurnPlan 模型与澄清原因。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from edu_assistant.plan.command import Command, UnknownCommand


@dataclass
class TaskTurnPlan:
    commands: list[Command] = field(default_factory=list)


@dataclass
class KnowledgeTurnPlan:
    intents: list[str] = field(default_factory=list)


@dataclass
class ChitchatTurnPlan:
    pass


@dataclass
class TurnPlan:
    task: TaskTurnPlan | None = None
    knowledge: KnowledgeTurnPlan | None = None
    chitchat: ChitchatTurnPlan | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "TurnPlan":
        if not isinstance(data, dict):
            return cls()
        task_data = data.get("task")
        knowledge_data = data.get("knowledge")
        chitchat_data = data.get("chitchat")

        task: TaskTurnPlan | None = None
        if isinstance(task_data, dict):
            raw_commands = task_data.get("commands")
            commands: list[Command] = []
            if isinstance(raw_commands, list):
                for item in raw_commands:
                    if isinstance(item, dict):
                        commands.append(Command.from_dict(item))
            task = TaskTurnPlan(commands=commands)

        knowledge: KnowledgeTurnPlan | None = None
        if isinstance(knowledge_data, dict):
            raw_intents = knowledge_data.get("intents")
            intents = (
                [str(x) for x in raw_intents if x]
                if isinstance(raw_intents, list)
                else []
            )
            knowledge = KnowledgeTurnPlan(intents=intents)

        chitchat = ChitchatTurnPlan() if chitchat_data is not None else None
        return cls(task=task, knowledge=knowledge, chitchat=chitchat)


class ClarifyReason(Enum):
    MISSING_TRACK = "missing_track"
    MULTIPLE_TRACKS = "multiple_tracks"
    MISSING_TASK_COMMANDS = "missing_task_commands"
    MISSING_KNOWLEDGE_INTENT = "missing_knowledge_intent"
    MISSING_FOCUSED_OBJECT = "missing_focused_object"
    OBJECT_REQUIRES_INTENT = "object_requires_intent"
    OBJECT_TYPE_MISMATCH = "object_type_mismatch"
    INVALID_TASK_COMMAND = "invalid_task_command"
    UNKNOWN_KNOWLEDGE_INTENT = "unknown_knowledge_intent"
    PLAN_FAILED = "plan_failed"
    TOO_MANY_PAUSED_TASKS = "too_many_paused_tasks"
    AMBIGUOUS_INPUT = "ambiguous_input"


@dataclass
class TurnPlanValidationResult:
    valid: bool
    reason: ClarifyReason | None = None
    detail: str = ""  # 日志用细节，不透给用户
