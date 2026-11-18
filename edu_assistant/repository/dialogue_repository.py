"""对话仓储：会话、消息、状态、事件、轨迹的持久化与查询。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from edu_assistant.domain.message import MessageObject
from edu_assistant.domain.state import (
    DialogueState,
    SessionCache,
    TaskInstance,
    TaskState,
)
from edu_assistant.repository.orm.models import (
    DialogueMessageRecord,
    DialogueSessionRecord,
    DialogueStateRecord,
    DialogueTraceRecord,
    TaskEventRecord,
)


def state_to_json(state: DialogueState) -> str:
    from dataclasses import asdict

    return json.dumps(asdict(state), ensure_ascii=False, default=str)


def _load_task(raw: dict) -> TaskInstance:
    return TaskInstance(
        flow_id=str(raw.get("flow_id", "")),
        flow_name=str(raw.get("flow_name", "")),
        step_id=raw.get("step_id"),
        task_id=str(raw.get("task_id") or ""),
        slots=raw.get("slots") or {},
        wait_refund_link=bool(raw.get("wait_refund_link", False)),
        resume_step_id=str(raw.get("resume_step_id") or ""),
        created_at=float(raw.get("created_at") or 0.0),
    )


def state_from_json(raw: str, sender_id: str, session_id: str) -> DialogueState:
    """反序列化状态；新增字段必须有默认值，旧状态可解析。"""
    data = json.loads(raw)

    cache_raw = data.get("session_cache") or {}
    tasks_raw = data.get("tasks") or {}

    tasks = TaskState()
    if tasks_raw.get("active"):
        tasks.active = _load_task(tasks_raw["active"])
    for item in tasks_raw.get("paused") or []:
        tasks.paused.append(_load_task(item))

    focused_raw = data.get("focused_object")
    focused: MessageObject | None = None
    if isinstance(focused_raw, dict):
        focused = MessageObject(
            id=str(focused_raw.get("id", "")),
            type=str(focused_raw.get("type", "")),
            title=focused_raw.get("title"),
            attributes=focused_raw.get("attributes") or {},
        )

    return DialogueState(
        sender_id=sender_id,
        session_id=session_id,
        session_cache=SessionCache(
            student_id=cache_raw.get("student_id"),
            student_profile=cache_raw.get("student_profile"),
            last_cohort=cache_raw.get("last_cohort"),
        ),
        tasks=tasks,
        focused_object=focused,
        history=list(data.get("history") or []),
        state_version=int(data.get("state_version") or 0),
    )


class DialogueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # —— 会话 ——

    async def create_session(self, session_id: str, user_id: str) -> DialogueSessionRecord:
        record = DialogueSessionRecord(id=session_id, user_id=str(user_id), status="active")
        self.session.add(record)
        await self.session.commit()
        return record

    async def get_session(self, session_id: str) -> DialogueSessionRecord | None:
        result = await self.session.execute(
            select(DialogueSessionRecord).where(DialogueSessionRecord.id == session_id)
        )
        return result.scalar_one_or_none()

    async def ensure_session_owned(
        self, session_id: str, user_id: str
    ) -> DialogueSessionRecord | None:
        """校验会话存在且属于当前用户；不存在或不属于返回 None。"""
        record = await self.get_session(session_id)
        if record is None:
            return None
        if str(record.user_id) != str(user_id):
            return None
        return record

    # —— 状态 ——

    async def load_state(self, session_id: str, user_id: str) -> DialogueState:
        result = await self.session.execute(
            select(DialogueStateRecord).where(DialogueStateRecord.session_id == session_id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return DialogueState(sender_id=str(user_id), session_id=session_id)
        return state_from_json(record.state_json, str(user_id), session_id)

    async def save_state(self, state: DialogueState) -> None:
        state.state_version += 1
        result = await self.session.execute(
            select(DialogueStateRecord).where(DialogueStateRecord.session_id == state.session_id)
        )
        record = result.scalar_one_or_none()
        state_json = state_to_json(state)
        if record is None:
            self.session.add(
                DialogueStateRecord(
                    session_id=state.session_id,
                    user_id=state.sender_id,
                    state_version=state.state_version,
                    state_json=state_json,
                )
            )
        else:
            record.user_id = state.sender_id
            record.state_version = state.state_version
            record.state_json = state_json
        await self.session.commit()

    # —— 消息 ——

    async def get_user_message_by_client_id(
        self, session_id: str, client_message_id: str
    ) -> DialogueMessageRecord | None:
        """messageId 防重：命中说明该消息已处理过。"""
        result = await self.session.execute(
            select(DialogueMessageRecord)
            .where(
                DialogueMessageRecord.session_id == session_id,
                DialogueMessageRecord.client_message_id == client_message_id,
                DialogueMessageRecord.role == "user",
            )
            .order_by(desc(DialogueMessageRecord.id))
        )
        return result.scalar_one_or_none()

    async def get_assistant_messages_after(
        self, session_id: str, user_message_id: int
    ) -> list[DialogueMessageRecord]:
        result = await self.session.execute(
            select(DialogueMessageRecord)
            .where(
                DialogueMessageRecord.session_id == session_id,
                DialogueMessageRecord.id > user_message_id,
                DialogueMessageRecord.role == "assistant",
            )
            .order_by(DialogueMessageRecord.id)
        )
        return list(result.scalars().all())

    async def add_message(
        self,
        session_id: str,
        client_message_id: str,
        role: str,
        message_type: str,
        content: dict,
        turn_no: int,
        request_id: str = "",
    ) -> DialogueMessageRecord:
        record = DialogueMessageRecord(
            session_id=session_id,
            client_message_id=client_message_id,
            request_id=request_id,
            role=role,
            message_type=message_type,
            content=content,
            turn_no=turn_no,
        )
        self.session.add(record)
        await self.session.commit()
        return record

    async def list_messages(self, session_id: str, limit: int = 200) -> list[DialogueMessageRecord]:
        result = await self.session.execute(
            select(DialogueMessageRecord)
            .where(DialogueMessageRecord.session_id == session_id)
            .order_by(DialogueMessageRecord.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    # —— 任务事件 ——

    async def add_task_event(
        self,
        session_id: str,
        request_id: str,
        event_type: str,
        task_id: str,
        flow_id: str,
        payload: dict | None = None,
    ) -> None:
        self.session.add(
            TaskEventRecord(
                session_id=session_id,
                request_id=request_id,
                event_type=event_type,
                task_id=task_id,
                flow_id=flow_id,
                payload=payload or {},
            )
        )
        await self.session.commit()

    # —— 轨迹 ——

    async def add_trace(
        self,
        request_id: str,
        session_id: str,
        turn_no: int,
        track: str,
        plan_summary: str,
        actions: list[dict],
        status: str,
        error_code: str,
        duration_ms: int,
    ) -> None:
        self.session.add(
            DialogueTraceRecord(
                request_id=request_id,
                session_id=session_id,
                turn_no=turn_no,
                track=track,
                plan_summary=plan_summary[:2000],
                actions=actions,
                status=status,
                error_code=error_code,
                duration_ms=duration_ms,
            )
        )
        await self.session.commit()

    async def list_traces(self, session_id: str, limit: int = 50) -> list[DialogueTraceRecord]:
        result = await self.session.execute(
            select(DialogueTraceRecord)
            .where(DialogueTraceRecord.session_id == session_id)
            .order_by(desc(DialogueTraceRecord.id))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_trace(self, request_id: str) -> DialogueTraceRecord | None:
        result = await self.session.execute(
            select(DialogueTraceRecord).where(DialogueTraceRecord.request_id == request_id)
        )
        return result.scalar_one_or_none()


def message_content_dict(record: DialogueMessageRecord) -> dict[str, Any]:
    """消息记录 → API 返回格式。"""
    content = record.content or {}
    return {
        "messageId": record.id,
        "clientMessageId": record.client_message_id,
        "role": record.role,
        "type": record.message_type,
        "text": content.get("text"),
        "object": content.get("object"),
        "turnNo": record.turn_no,
        "createdAt": (
            record.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(record.created_at, datetime)
            else None
        ),
    }
