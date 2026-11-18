"""会话路由：创建会话、历史、状态、轨迹。均校验会话归属。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from edu_assistant.api.schemas import (
    HistoryResponse,
    SessionCreateResponse,
    StateResponse,
    TraceDetail,
    TraceItem,
)
from edu_assistant.service.dialogue_service import DialogueService
from edu_assistant.api.dependencies import get_dialogue_service

router = APIRouter()

UserIdHeader = Annotated[str | None, Header(alias="X-User-Id")]


def _require_user_id(x_user_id: str | None) -> str:
    if not x_user_id or not x_user_id.strip():
        from edu_assistant.utils.errors import unauthorized

        raise unauthorized("MISSING_USER_ID", "缺少请求头 X-User-Id")
    value = x_user_id.strip()
    if not value.isdigit():
        from edu_assistant.utils.errors import unauthorized

        raise unauthorized("INVALID_USER_ID", "X-User-Id 必须为数字用户 ID")
    return value


@router.post("/api/sessions", response_model=SessionCreateResponse)
async def create_session(
    dialogue_service: DialogueService = Depends(get_dialogue_service),
    x_user_id: UserIdHeader = None,
) -> SessionCreateResponse:
    user_id = _require_user_id(x_user_id)
    result = await dialogue_service.create_session(user_id)
    return SessionCreateResponse(**result)


@router.get("/api/sessions/{session_id}/history", response_model=HistoryResponse)
async def get_history(
    session_id: str,
    dialogue_service: DialogueService = Depends(get_dialogue_service),
    x_user_id: UserIdHeader = None,
) -> HistoryResponse:
    user_id = _require_user_id(x_user_id)
    messages = await dialogue_service.get_history(session_id, user_id)
    return HistoryResponse(sessionId=session_id, messages=messages)


@router.get("/api/sessions/{session_id}/state", response_model=StateResponse)
async def get_state(
    session_id: str,
    dialogue_service: DialogueService = Depends(get_dialogue_service),
    x_user_id: UserIdHeader = None,
) -> StateResponse:
    user_id = _require_user_id(x_user_id)
    state = await dialogue_service.get_state(session_id, user_id)
    return StateResponse(**state)


@router.get("/api/sessions/{session_id}/traces", response_model=list[TraceItem])
async def get_traces(
    session_id: str,
    dialogue_service: DialogueService = Depends(get_dialogue_service),
    x_user_id: UserIdHeader = None,
) -> list[TraceItem]:
    user_id = _require_user_id(x_user_id)
    traces = await dialogue_service.get_traces(session_id, user_id)
    return [TraceItem(**t) for t in traces]


@router.get("/api/traces/{request_id}", response_model=TraceDetail)
async def get_trace(
    request_id: str,
    dialogue_service: DialogueService = Depends(get_dialogue_service),
    x_user_id: UserIdHeader = None,
) -> TraceDetail:
    """按 request_id 查询单条处理轨迹（校验所属会话归属）。"""
    user_id = _require_user_id(x_user_id)
    trace = await dialogue_service.get_trace(request_id, user_id)
    return TraceDetail(**trace)
