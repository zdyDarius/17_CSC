"""闲聊兜底：自然回应并适当引导回教育服务场景。"""

from __future__ import annotations

from langchain_core.prompts import PromptTemplate

from edu_assistant.domain.message import BotMessage, UserMessage
from edu_assistant.domain.state import DialogueState
from edu_assistant.prompts.loader import load_prompt
from edu_assistant.utils.llm import get_llm
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


class ChitChatHandler:
    async def chit_chat(
        self, state: DialogueState, user_message: UserMessage
    ) -> list[BotMessage]:
        text = (user_message.text or "").strip()
        active = state.tasks.active
        active_task_text = ""
        if active is not None:
            active_task_text = f"{active.flow_name}（step={active.step_id}）"
        try:
            prompt = PromptTemplate.from_template(
                load_prompt("chitchat_respond"), template_format="jinja2"
            )
            chain = prompt | get_llm()
            result = await chain.ainvoke(
                input={
                    "user_text": text[:500],
                    "active_task_text": active_task_text,
                }
            )
            content = (getattr(result, "content", "") or "").strip()
            if content:
                return [BotMessage(text=content)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("闲聊回复失败，使用兜底文案: %s", exc)
        return [
            BotMessage(
                text="你好呀～我是教育平台智能助理小度。我可以帮你：查询订单状态、查询学习进度、申请退款、提交工单，也可以介绍课程、班次和平台政策。想从哪一项开始？"
            )
        ]
