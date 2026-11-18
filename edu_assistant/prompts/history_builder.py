"""有限历史构建：把 state.history 渲染为规划器可用的对话文本。"""

from __future__ import annotations

from edu_assistant.conf.config import get_settings
from edu_assistant.domain.message import UserMessage
from edu_assistant.domain.state import DialogueState


def build_history_text(state: DialogueState) -> str:
    """最近 N 轮历史 → 文本（user/assistant 交替）。"""
    if not state.history:
        return "（无历史对话）"
    max_turns = get_settings().max_history_turns
    items = state.history[-(max_turns * 2) :]
    lines: list[str] = []
    for item in items:
        role = "用户" if item.get("role") == "user" else "助理"
        content = str(item.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) if lines else "（无历史对话）"


def render_user_message(user_message: UserMessage) -> str:
    if user_message.type == "object" and user_message.object is not None:
        obj = user_message.object
        return f"[发送了业务对象] 类型: {obj.type}，ID: {obj.id}，名称: {obj.title or '无'}"
    return (user_message.text or "").strip()


def build_task_state_summary(state: DialogueState) -> dict:
    """任务状态摘要（给规划器，信息最小化：不含步骤详情）。"""
    tasks = state.tasks
    active_summary = None
    if tasks.active is not None:
        active = tasks.active
        active_summary = {
            "task_id": active.task_id,
            "flow_id": active.flow_id,
            "flow_name": active.flow_name,
            "step_id": active.step_id,
            "collected_slots": {
                k: v for k, v in (active.slots or {}).items()
                if not k.startswith("_") and v not in (None, "", [], {})
                # 只暴露槽位名集合的值摘要，敏感内部字段不下发
            },
        }
    paused_summary = [
        {
            "task_id": t.task_id,
            "flow_id": t.flow_id,
            "flow_name": t.flow_name,
            "step_id": t.step_id,
        }
        for t in tasks.paused
    ]
    return {"active": active_summary, "paused": paused_summary}
