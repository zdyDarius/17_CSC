"""任务生命周期事件与中文提示。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskRef:
    task_id: str
    flow_id: str
    name: str = ""


@dataclass
class TaskEvent:
    """任务状态变更事件基类。"""


@dataclass
class TaskStarted(TaskEvent):
    task: TaskRef


@dataclass
class TaskSwitched(TaskEvent):
    previous: TaskRef
    current: TaskRef


@dataclass
class TaskResumed(TaskEvent):
    task: TaskRef


@dataclass
class TaskCanceled(TaskEvent):
    task: TaskRef


@dataclass
class TaskCompleted(TaskEvent):
    task: TaskRef


# 事件 → 用户可见中文提示
EVENT_PROMPTS: dict[str, str] = {
    "started": "好的，我们开始「{name}」。",
    "switched": "已为你切换到「{current}」，刚才的「{previous}」先暂停，结束后可以继续。",
    "resumed": "继续刚才的「{name}」。",
    "canceled": "已取消「{name}」，相关信息已清理。",
    "completed": "「{name}」已完成。",
}


def event_prompt(event: TaskEvent, flow_names: dict[str, str]) -> str | None:
    """把任务事件渲染成中文提示；无提示时返回 None。"""
    name_of = lambda ref: ref.name or flow_names.get(ref.flow_id, ref.flow_id)  # noqa: E731

    if isinstance(event, TaskStarted):
        return EVENT_PROMPTS["started"].format(name=name_of(event.task))
    if isinstance(event, TaskSwitched):
        return EVENT_PROMPTS["switched"].format(
            current=name_of(event.current), previous=name_of(event.previous)
        )
    if isinstance(event, TaskResumed):
        return EVENT_PROMPTS["resumed"].format(name=name_of(event.task))
    if isinstance(event, TaskCanceled):
        return EVENT_PROMPTS["canceled"].format(name=name_of(event.task))
    if isinstance(event, TaskCompleted):
        return EVENT_PROMPTS["completed"].format(name=name_of(event.task))
    return None


def event_record(event: TaskEvent) -> dict:
    """TaskEvent → task_event 表记录字段（审计用）。"""
    ref = None
    if isinstance(event, (TaskStarted, TaskResumed, TaskCanceled, TaskCompleted)):
        ref = event.task
    elif isinstance(event, TaskSwitched):
        ref = event.current
    if ref is None:
        return {"event_type": "unknown", "task_id": "", "flow_id": "", "payload": {}}
    payload: dict = {"flow_name": ref.name}
    if isinstance(event, TaskSwitched):
        payload["previous"] = {"task_id": event.previous.task_id, "flow_id": event.previous.flow_id}
    return {
        "event_type": type(event).__name__,
        "task_id": ref.task_id,
        "flow_id": ref.flow_id,
        "payload": payload,
    }
