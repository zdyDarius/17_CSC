"""对话消息模型：用户消息（文本/业务对象）、客服回复、处理结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MessageType(str, Enum):
    TEXT = "text"
    OBJECT = "object"


# 业务对象消息白名单类型（项目计划 4.3）
OBJECT_TYPES = ("order", "series", "cohort", "refund_request")


@dataclass
class MessageObject:
    """业务对象消息载荷。"""

    id: str
    type: str  # order / series / cohort / refund_request
    title: str | None = None
    attributes: dict = field(default_factory=dict)


@dataclass
class UserMessage:
    sender_id: str  # 服务端绑定的 user_id（来自 X-User-Id）
    message_id: str  # 客户端幂等 ID
    session_id: str = ""
    type: MessageType = MessageType.TEXT
    text: str | None = None
    object: MessageObject | None = None

    def short_text(self) -> str:
        if self.type == MessageType.OBJECT and self.object is not None:
            return f"[对象:{self.object.type}:{self.object.id}] {self.object.title or ''}".strip()
        return (self.text or "").strip()


@dataclass
class BotMessage:
    text: str
    object: dict | None = None  # 可选回传业务对象（如班次卡片）
