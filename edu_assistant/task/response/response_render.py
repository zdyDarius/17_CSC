"""回复渲染：Jinja2 模板渲染；渲染失败时兜底通用文案，禁止返回 None。"""

from __future__ import annotations

import time

from jinja2 import Environment
from jinja2.sandbox import SandboxedEnvironment

from edu_assistant.domain.message import BotMessage, UserMessage
from edu_assistant.domain.state import DialogueState

# 沙箱化 Jinja2（默认 Undefined：未定义变量在 {% if %} 中为假，渲染为空，不抛异常）
_env = SandboxedEnvironment(autoescape=False, trim_blocks=True)
_plain_env = Environment(autoescape=False, trim_blocks=True)


class ResponseRender:
    def render(
        self,
        template_text: str,
        state: DialogueState,
        user_message: UserMessage,
    ) -> BotMessage:
        slots: dict = {}
        if state.tasks.active is not None:
            slots = dict(state.tasks.active.slots or {})
        context = {
            "slots": slots,
            "user": {"id": user_message.sender_id},
            "text": user_message.text or "",
            "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            content = _env.from_string(template_text).render(**context)
        except Exception:
            # 模板渲染失败：兜底通用文案，不把异常抛成 500
            try:
                content = _plain_env.from_string(template_text).render(**context)
            except Exception:
                content = "抱歉，这条回复生成失败了。你可以换个说法，或输入「人工客服」寻求帮助。"
        content = (content or "").strip()
        if not content:
            content = "抱歉，这条回复生成为空。你可以换个说法再试一次。"
        return BotMessage(
            text=content,
            object=(
                {
                    "type": state.focused_object.type,
                    "id": state.focused_object.id,
                    "title": state.focused_object.title,
                }
                if state.focused_object is not None
                else None
            ),
        )
