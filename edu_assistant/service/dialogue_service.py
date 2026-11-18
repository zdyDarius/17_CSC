"""对话服务：取状态 → 调引擎 → 校验结果 → 持久化。

- messageId 防重：同一会话重复提交同一 client_message_id 直接返回首次结果，不重复执行 Action。
- 同一会话串行：每个会话一把 asyncio.Lock，避免并发推进 step_id 或重复写操作。
- 状态写库失败：响应标记 degraded=true 并记录告警（闲聊/只读可接受，写任务暂停提示重试）。
- trace：request_id 串联规划摘要、轨道、动作、耗时。
"""

from __future__ import annotations

import asyncio
import time
import uuid

from edu_assistant.domain.message import BotMessage, MessageObject, MessageType, UserMessage
from edu_assistant.domain.state import DialogueState
from edu_assistant.engine.builder import EngineComponents
from edu_assistant.lifecycle.models import event_record
from edu_assistant.repository.dialogue_repository import (
    DialogueRepository,
    message_content_dict,
)
from edu_assistant.utils.db import get_session_factory
from edu_assistant.utils.errors import AppError, not_found
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

WELCOME_TEXT = (
    "你好，我是教育平台智能助理小度。我可以帮你：\n"
    "1. 查询订单状态（告诉我订单号即可）\n"
    "2. 查询学习进度（告诉我班次名即可）\n"
    "3. 申请退款（我会引导你确认明细和金额）\n"
    "4. 提交工单（售后/投诉/退款类）\n"
    "也可以问我课程介绍、退款政策、报名与开课政策、平台使用指南。请问需要什么帮助？"
)

# 会话级串行锁。
# 边界说明：仅在单进程内有效（多 worker 部署需改用数据库锁或外部锁）；
# 字典随会话数增长，超过上限时清理未持锁条目，避免长期运行内存泄漏。
_session_locks: dict[str, asyncio.Lock] = {}
MAX_SESSION_LOCKS = 512


def _get_session_lock(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        if len(_session_locks) >= MAX_SESSION_LOCKS:
            idle = [k for k, v in _session_locks.items() if not v.locked()]
            for key in idle[: len(idle) // 2]:
                _session_locks.pop(key, None)
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


class DialogueService:
    def __init__(self, components: EngineComponents) -> None:
        self.components = components
        self.engine = components.engine

    def _repo(self) -> DialogueRepository:
        return DialogueRepository(get_session_factory()())

    # —— 会话 ——

    async def create_session(self, user_id: str) -> dict:
        session_id = uuid.uuid4().hex
        request_id = uuid.uuid4().hex
        repo = self._repo()
        await repo.create_session(session_id, user_id)
        # 欢迎语直接落库为首条 assistant 消息（不占用流程栈）
        await repo.add_message(
            session_id=session_id,
            client_message_id=f"welcome-{session_id[:12]}",
            role="assistant",
            message_type="text",
            content={"text": WELCOME_TEXT},
            turn_no=0,
            request_id=request_id,
        )
        await repo.add_trace(
            request_id=request_id,
            session_id=session_id,
            turn_no=0,
            track="welcome",
            plan_summary="create_session",
            actions=[],
            status="ok",
            error_code="",
            duration_ms=0,
        )
        return {
            "sessionId": session_id,
            "userId": user_id,
            "welcomeText": WELCOME_TEXT,
            "requestId": request_id,
        }

    # —— 聊天 ——

    async def process_message(self, user_message: UserMessage) -> dict:
        session_id = user_message.session_id
        lock = _get_session_lock(session_id)
        request_id = uuid.uuid4().hex
        start_ms = time.perf_counter()

        async with lock:
            repo = self._repo()
            try:
                # 1 会话归属校验
                session = await repo.ensure_session_owned(session_id, user_message.sender_id)
                if session is None:
                    raise not_found("SESSION_NOT_FOUND", "会话不存在或无权访问")

                # 2 messageId 防重：重复提交同一 client_message_id 返回首次结果
                existing = await repo.get_user_message_by_client_id(
                    session_id, user_message.message_id
                )
                if existing is not None:
                    assistant_rows = await repo.get_assistant_messages_after(session_id, existing.id)
                    return {
                        "requestId": existing.request_id or request_id,
                        "sessionId": session_id,
                        "messageId": user_message.message_id,
                        "duplicated": True,
                        "messages": [
                            {
                                "text": (row.content or {}).get("text", ""),
                                "object": (row.content or {}).get("object"),
                            }
                            for row in assistant_rows
                        ],
                    }

                # 3 取状态
                state = await repo.load_state(session_id, user_message.sender_id)

                # 4 调引擎
                outcome = await self.engine.process_message(state, user_message)
                messages = [m for m in outcome.messages if m is not None and (m.text or "").strip()]
                if not messages:
                    messages = [BotMessage(text="抱歉，我暂时没能生成回复，请换个说法再试一次。")]

                # 5 校验通过后持久化：先更新历史摘要与状态，再落消息
                turn_no = await repo.next_turn_no(session_id)
                user_content = (
                    {"text": user_message.text}
                    if user_message.type == MessageType.TEXT
                    else {"object": vars(user_message.object)}
                    if user_message.object
                    else {"text": ""}
                )
                # 历史摘要（规划器有限历史）
                state.append_history("user", user_message.short_text())
                for bot_message in messages:
                    state.append_history("assistant", bot_message.text)

                degraded = False
                try:
                    await repo.save_state(state)
                except Exception as exc:  # noqa: BLE001
                    degraded = True
                    logger.error("状态写库失败 session=%s: %s", session_id, exc)
                    # 八.4 降级策略：写库失败时明确提示，退款/工单类写操作暂停稍后重试
                    messages.append(
                        BotMessage(
                            text=(
                                "⚠️ 提示：对话状态保存失败，本轮回复可正常查看，但任务进度可能未保存。"
                                "为避免重复操作，退款、工单类写入请稍后重试；涉及退款的可先在「我的退款」中核对是否已提交成功。"
                            )
                        )
                    )

                await repo.add_message(
                    session_id=session_id,
                    client_message_id=user_message.message_id,
                    role="user",
                    message_type=user_message.type.value,
                    content=user_content,
                    turn_no=turn_no,
                    request_id=request_id,
                )
                for index, bot_message in enumerate(messages):
                    await repo.add_message(
                        session_id=session_id,
                        client_message_id=f"{user_message.message_id}#{index}",
                        role="assistant",
                        message_type="text",
                        content={"text": bot_message.text, "object": bot_message.object},
                        turn_no=turn_no,
                        request_id=request_id,
                    )

                await repo.add_trace(
                    request_id=request_id,
                    session_id=session_id,
                    turn_no=turn_no,
                    track=outcome.track,
                    plan_summary=outcome.plan_summary,
                    actions=[{"action": a} for a in outcome.actions],
                    status="degraded" if degraded else "ok",
                    error_code="STATE_WRITE_FAILED" if degraded else "",
                    duration_ms=int((time.perf_counter() - start_ms) * 1000),
                )
                # 任务生命周期事件落 task_event 表（审计）
                for event in outcome.events:
                    record = event_record(event)
                    await repo.add_task_event(
                        session_id=session_id,
                        request_id=request_id,
                        event_type=record["event_type"],
                        task_id=record["task_id"],
                        flow_id=record["flow_id"],
                        payload=record["payload"],
                    )

                return {
                    "requestId": request_id,
                    "sessionId": session_id,
                    "messageId": user_message.message_id,
                    "duplicated": False,
                    "degraded": degraded,
                    "track": outcome.track,
                    "messages": [
                        {"text": m.text, "object": m.object} for m in messages
                    ],
                }
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("消息处理异常 session=%s", session_id)
                await self._safe_trace(
                    request_id=request_id,
                    session_id=session_id,
                    track="error",
                    plan_summary="internal_error",
                    actions=[],
                    status="error",
                    error_code="INTERNAL_ERROR",
                    duration_ms=int((time.perf_counter() - start_ms) * 1000),
                )
                return {
                    "requestId": request_id,
                    "sessionId": session_id,
                    "messageId": user_message.message_id,
                    "duplicated": False,
                    "degraded": True,
                    "track": "error",
                    "messages": [
                        {
                            "text": "抱歉，系统处理出现异常，请稍后重试；如持续出现，可提交工单由人工客服跟进。",
                            "object": None,
                        }
                    ],
                }

    async def _safe_trace(self, **kwargs) -> None:
        try:
            repo = self._repo()
            await repo.add_trace(**kwargs)
        except Exception:  # noqa: BLE001
            logger.warning("trace 写入失败", exc_info=True)

    # —— 历史 / 状态 / 轨迹 ——

    async def get_history(self, session_id: str, user_id: str) -> list[dict]:
        repo = self._repo()
        if await repo.ensure_session_owned(session_id, user_id) is None:
            raise not_found("SESSION_NOT_FOUND", "会话不存在或无权访问")
        rows = await repo.list_messages(session_id)
        return [message_content_dict(row) for row in rows]

    async def get_state(self, session_id: str, user_id: str) -> dict:
        repo = self._repo()
        if await repo.ensure_session_owned(session_id, user_id) is None:
            raise not_found("SESSION_NOT_FOUND", "会话不存在或无权访问")
        state = await repo.load_state(session_id, user_id)
        active = state.tasks.active
        paused = state.tasks.paused

        def _safe_slots(slots: dict) -> dict:
            # 非敏感槽位：隐藏内部字段与超大字段
            return {
                k: v for k, v in (slots or {}).items()
                if not k.startswith("_") and len(str(v)) < 500
            }

        return {
            "sessionId": session_id,
            "userId": user_id,
            "activeTask": (
                {
                    "taskId": active.task_id,
                    "flowId": active.flow_id,
                    "flowName": active.flow_name,
                    "stepId": active.step_id,
                    "slots": _safe_slots(active.slots),
                }
                if active
                else None
            ),
            "pausedTasks": [
                {
                    "taskId": t.task_id,
                    "flowId": t.flow_id,
                    "flowName": t.flow_name,
                    "stepId": t.step_id,
                    "slots": _safe_slots(t.slots),
                    "waitRefundLink": t.wait_refund_link,
                }
                for t in paused
            ],
            "focusedObject": (
                {"type": state.focused_object.type, "id": state.focused_object.id, "title": state.focused_object.title}
                if state.focused_object
                else None
            ),
            "stateVersion": state.state_version,
        }

    async def get_traces(self, session_id: str, user_id: str) -> list[dict]:
        repo = self._repo()
        if await repo.ensure_session_owned(session_id, user_id) is None:
            raise not_found("SESSION_NOT_FOUND", "会话不存在或无权访问")
        rows = await repo.list_traces(session_id)
        return [
            {
                "requestId": row.request_id,
                "turnNo": row.turn_no,
                "track": row.track,
                "planSummary": row.plan_summary,
                "actions": row.actions,
                "status": row.status,
                "errorCode": row.error_code,
                "durationMs": row.duration_ms,
                "createdAt": str(row.created_at),
            }
            for row in rows
        ]

    async def get_trace(self, request_id: str, user_id: str) -> dict:
        """按 request_id 查询单条轨迹（校验所属会话归属）。"""
        repo = self._repo()
        row = await repo.get_trace(request_id)
        if row is None:
            raise not_found("TRACE_NOT_FOUND", "轨迹不存在")
        if await repo.ensure_session_owned(row.session_id, user_id) is None:
            raise not_found("SESSION_NOT_FOUND", "会话不存在或无权访问")
        return {
            "requestId": row.request_id,
            "sessionId": row.session_id,
            "turnNo": row.turn_no,
            "track": row.track,
            "planSummary": row.plan_summary,
            "actions": row.actions,
            "status": row.status,
            "errorCode": row.error_code,
            "durationMs": row.duration_ms,
            "createdAt": str(row.created_at),
        }
