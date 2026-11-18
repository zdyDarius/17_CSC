"""聊天路由：非流式 / SSE 流式。

SSE 事件：meta / delta / done / error。
MVP 策略：先得到经过校验的完整回复，再切成文本片段流式发送；
规划器 JSON 不直接流式暴露；完整回复只持久化一次（service 内完成），
done 携带 requestId 与完整消息。客户端断开不回滚已完成的退款/工单动作。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from edu_assistant.api.dependencies import get_dialogue_service
from edu_assistant.api.schemas import ChatRequest, ChatResponse
from edu_assistant.conf.config import get_settings
from edu_assistant.domain.message import (
    OBJECT_TYPES,
    MessageObject,
    MessageType,
    UserMessage,
)
from edu_assistant.service.dialogue_service import DialogueService
from edu_assistant.utils.errors import bad_request, not_found, unauthorized

router = APIRouter()

UserIdHeader = Annotated[str | None, Header(alias="X-User-Id")]


def _current_user_id(x_user_id: str | None) -> str:
    """X-User-Id 必填且为数字（存在性由 edu-data API 校验）。"""
    if not x_user_id or not x_user_id.strip():
        raise unauthorized("MISSING_USER_ID", "缺少请求头 X-User-Id")
    value = x_user_id.strip()
    if not value.isdigit():
        raise unauthorized("INVALID_USER_ID", "X-User-Id 必须为数字用户 ID")
    return value


def _build_user_message(chat_request: ChatRequest, user_id: str) -> UserMessage:
    has_text = bool((chat_request.text or "").strip())
    has_object = chat_request.object is not None
    if has_text and has_object:
        raise bad_request("TEXT_OR_OBJECT", "text 与 object 只能二选一")
    if not has_text and not has_object:
        raise bad_request("EMPTY_MESSAGE", "消息内容为空")

    if has_object:
        obj = chat_request.object
        if obj.type not in OBJECT_TYPES:
            raise bad_request(
                "INVALID_OBJECT_TYPE",
                f"对象类型必须是 {'/'.join(OBJECT_TYPES)} 之一",
            )
        return UserMessage(
            sender_id=user_id,
            message_id=chat_request.messageId,
            session_id=chat_request.sessionId,
            type=MessageType.OBJECT,
            object=MessageObject(
                id=str(obj.id),
                type=obj.type,
                title=obj.title,
                attributes=obj.attributes or {},
            ),
        )
    settings = get_settings()
    if len(chat_request.text or "") > settings.max_text_length:
        raise bad_request("TEXT_TOO_LONG", f"消息长度超过上限 {settings.max_text_length}")
    return UserMessage(
        sender_id=user_id,
        message_id=chat_request.messageId,
        session_id=chat_request.sessionId,
        type=MessageType.TEXT,
        text=chat_request.text,
    )


@router.post("/api/chat", response_model=ChatResponse)
async def chat(
    chat_request: ChatRequest,
    dialogue_service: DialogueService = Depends(get_dialogue_service),
    x_user_id: UserIdHeader = None,
) -> ChatResponse:
    user_id = _current_user_id(x_user_id)
    user_message = _build_user_message(chat_request, user_id)
    result = await dialogue_service.process_message(user_message)
    return ChatResponse(
        requestId=result["requestId"],
        sessionId=result["sessionId"],
        messageId=result["messageId"],
        duplicated=result.get("duplicated", False),
        degraded=result.get("degraded", False),
        track=result.get("track"),
        messages=result["messages"],
    )


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunk_text(text: str, size: int = 12) -> list[str]:
    """完整回复切成小片段（逐字展示效果）。"""
    chunks: list[str] = []
    for start in range(0, len(text), size):
        chunks.append(text[start : start + size])
    # 按行优先切分，保持换行体验
    if "\n" in text:
        chunks = []
        for line in text.split("\n"):
            for start in range(0, len(line), size):
                chunks.append(line[start : start + size])
            chunks.append("\n")
        if chunks and chunks[-1] == "\n":
            chunks.pop()
    return chunks or [""]


@router.post("/api/chat/stream")
async def chat_stream(
    chat_request: ChatRequest,
    dialogue_service: DialogueService = Depends(get_dialogue_service),
    x_user_id: UserIdHeader = None,
) -> StreamingResponse:
    user_id = _current_user_id(x_user_id)
    user_message = _build_user_message(chat_request, user_id)

    async def event_stream() -> AsyncIterator[str]:
        try:
            result = await dialogue_service.process_message(user_message)
            request_id = result["requestId"]
            yield _sse_event(
                "meta",
                {
                    "requestId": request_id,
                    "sessionId": result["sessionId"],
                    "messageId": result["messageId"],
                    "track": result.get("track"),
                    "degraded": result.get("degraded", False),
                    "duplicated": result.get("duplicated", False),
                },
            )
            for item in result["messages"]:
                text = item.get("text") or ""
                for chunk in _chunk_text(text):
                    yield _sse_event("delta", {"text": chunk})
                    await asyncio.sleep(0.03)
            yield _sse_event(
                "done",
                {
                    "requestId": request_id,
                    "messageId": result["messageId"],
                    "messageCount": len(result["messages"]),
                    "messages": result["messages"],
                },
            )
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "message", "") or "流式处理异常"
            yield _sse_event("error", {"error": detail})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
