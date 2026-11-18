"""对话状态模型。

结构（持久化为 JSON，带 version，新增字段必须有默认值）：
- session_cache：会话级缓存（student_id 等，跨任务复用，不随任务取消清理）
- tasks：任务状态（active + paused 栈）
- focused_object：最近一条业务对象消息
- history：最近几轮对话摘要（用于规划器有限历史）

与参考项目相比的修复：
- resume/cancel 找不到任务时返回 None 而不是 raise（由澄清组件兜底，不产生 500）
- paused 栈有上限；start_task 栈满时返回 None 交由上层澄清
- TaskInstance 带 flow 名称快照与 wait_refund_link 标志（退款转工单恢复）
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from edu_assistant.domain.message import MessageObject
from edu_assistant.lifecycle.models import (
    TaskCanceled,
    TaskCompleted,
    TaskEvent,
    TaskRef,
    TaskResumed,
    TaskStarted,
    TaskSwitched,
)


@dataclass
class SessionCache:
    """会话级缓存：学员档案等，避免重复填写。"""

    student_id: int | None = None
    student_profile: dict | None = None
    # 最近一次进度查询的班次（供「考试分数」等机器人邀请的追问复用）
    last_cohort: dict | None = None


@dataclass
class TaskInstance:
    """一个正在执行/暂停的任务流程实例。"""

    flow_id: str
    flow_name: str = ""
    step_id: str | None = None
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    slots: dict = field(default_factory=dict)
    # 退款转工单：等待退款申请完成以便自动恢复的标志
    wait_refund_link: bool = False
    # 自动恢复时的重检步骤 ID（如工单流程的 check_refund 步骤）
    resume_step_id: str = ""
    created_at: float = field(default_factory=time.time)

    def get_ref(self) -> TaskRef:
        return TaskRef(task_id=self.task_id, flow_id=self.flow_id, name=self.flow_name)


@dataclass
class TaskState:
    """任务管控状态：切换压栈、恢复弹栈、取消清理。

    所有方法失败时返回 None，不抛异常，由上层澄清处理。
    """

    active: TaskInstance | None = None
    paused: list[TaskInstance] = field(default_factory=list)

    def start_task(self, task: TaskInstance, max_paused: int = 5) -> TaskEvent | None:
        if self.active is not None:
            if len(self.paused) >= max_paused:
                return None
            previous = self.active
            self.paused.append(self.active)
            self.active = task
            return TaskSwitched(previous=previous.get_ref(), current=task.get_ref())
        self.active = task
        return TaskStarted(task=task.get_ref())

    def resume_task(self, task_id: str) -> TaskEvent | None:
        target: TaskInstance | None = None
        for index, task in enumerate(self.paused):
            if task.task_id == task_id:
                target = self.paused.pop(index)
                break
        if target is None:
            return None
        if self.active is not None:
            previous = self.active
            self.paused.append(self.active)
            self.active = target
            return TaskSwitched(previous=previous.get_ref(), current=target.get_ref())
        self.active = target
        return TaskResumed(task=target.get_ref())

    def cancel_task(self, task_id: str) -> TaskEvent | None:
        if self.active is not None and self.active.task_id == task_id:
            ref = self.active.get_ref()
            self.active = None
            return TaskCanceled(task=ref)
        for task in self.paused:
            if task.task_id == task_id:
                ref = task.get_ref()
                self.paused.remove(task)
                return TaskCanceled(task=ref)
        return None

    def complete_active(self) -> TaskEvent | None:
        if self.active is None:
            return None
        ref = self.active.get_ref()
        self.active = None
        return TaskCompleted(task=ref)


@dataclass
class DialogueState:
    """一个会话的完整对话状态（历史消息存 dialogue_message 表，这里只存摘要）。"""

    sender_id: str = ""
    session_id: str = ""
    session_cache: SessionCache = field(default_factory=SessionCache)
    tasks: TaskState = field(default_factory=TaskState)
    focused_object: MessageObject | None = None
    # 最近几轮对话摘要 [{"role": "user"/"assistant", "content": "..."}]
    history: list[dict] = field(default_factory=list)
    state_version: int = 0  # 持久化乐观锁版本

    def append_history(self, role: str, content: str, max_turns: int = 12) -> None:
        if not content:
            return
        self.history.append({"role": role, "content": content[:500]})
        # 每轮 user+assistant 两条，保留 max_turns*2 条
        self.history = self.history[-(max_turns * 2) :]
