"""API 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatObject(BaseModel):
    """业务对象消息载荷（类型白名单见 domain.message.OBJECT_TYPES）。"""

    type: str = Field(description="对象类型：order / series / cohort / refund_request")
    id: str = Field(description="对象 ID（订单号为 ORD 单号；其余为数值 ID）")
    title: str | None = Field(default=None, description="对象展示名")
    attributes: dict = Field(default_factory=dict, description="附加属性")


class ChatRequest(BaseModel):
    sessionId: str = Field(description="会话 ID（POST /api/sessions 返回）")
    messageId: str = Field(description="客户端生成的消息唯一 ID，用于防重")
    text: str | None = Field(default=None, description="文本消息")
    object: ChatObject | None = Field(default=None, description="业务对象消息")

    model_config = {"extra": "ignore"}


class ChatMessageItem(BaseModel):
    text: str | None = None
    object: dict | None = None


class ChatResponse(BaseModel):
    requestId: str
    sessionId: str
    messageId: str
    duplicated: bool = False
    degraded: bool = False
    track: str | None = None
    messages: list[ChatMessageItem] = Field(default_factory=list)


class SessionCreateResponse(BaseModel):
    sessionId: str
    userId: str
    welcomeText: str
    requestId: str


class HistoryItem(BaseModel):
    messageId: int
    clientMessageId: str
    role: str
    type: str
    text: str | None = None
    object: dict | None = None
    turnNo: int
    createdAt: str | None = None


class HistoryResponse(BaseModel):
    sessionId: str
    messages: list[HistoryItem]


class StateResponse(BaseModel):
    sessionId: str
    userId: str
    activeTask: dict | None = None
    pausedTasks: list[dict] = Field(default_factory=list)
    focusedObject: dict | None = None
    stateVersion: int = 0


class TraceItem(BaseModel):
    requestId: str
    turnNo: int
    track: str
    planSummary: str
    actions: list
    status: str
    errorCode: str
    durationMs: int
    createdAt: str


class TraceDetail(TraceItem):
    sessionId: str
