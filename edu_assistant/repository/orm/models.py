"""客服系统自有表（MySQL + 异步 SQLAlchemy）。

- dialogue_session：会话与身份绑定
- dialogue_message：消息历史，(session_id, client_message_id) 唯一约束防重
- dialogue_state：对话状态 JSON（带状态版本号，并发安全由会话级串行锁保证）
- task_event：任务生命周期与关键动作审计
- dialogue_trace：处理轨迹（request_id 串联意图、轨道、动作、耗时）
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from edu_assistant.utils.db import get_session_factory


class Base(DeclarativeBase):
    pass


class DialogueSessionRecord(Base):
    __tablename__ = "dialogue_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class DialogueMessageRecord(Base):
    __tablename__ = "dialogue_message"
    __table_args__ = (
        UniqueConstraint("session_id", "client_message_id", name="uk_session_client_msg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    client_message_id: Mapped[str] = mapped_column(String(128))
    request_id: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    message_type: Mapped[str] = mapped_column(String(16))  # text / object
    content: Mapped[dict] = mapped_column(JSON)
    turn_no: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DialogueStateRecord(Base):
    __tablename__ = "dialogue_state"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now
    )


class TaskEventRecord(Base):
    __tablename__ = "task_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    event_type: Mapped[str] = mapped_column(String(32))
    task_id: Mapped[str] = mapped_column(String(64), default="")
    flow_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DialogueTraceRecord(Base):
    __tablename__ = "dialogue_trace"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    turn_no: Mapped[int] = mapped_column(Integer, default=0)
    track: Mapped[str] = mapped_column(String(32), default="")  # task/knowledge/chitchat/clarify
    plan_summary: Mapped[str] = mapped_column(Text, default="")
    actions: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="ok")  # ok/degraded/error
    error_code: Mapped[str] = mapped_column(String(64), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


async def init_tables() -> None:
    """启动时建表（幂等）。"""
    engine = get_session_factory().kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
