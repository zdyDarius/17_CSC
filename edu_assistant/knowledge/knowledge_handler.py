"""知识处理：意图 → 并行检索 → 组织答案。

- Provider 列表保序去重；独立 Provider 并行（return_exceptions=True 逐项降级）。
- 无依据时明确说不知道并建议转人工，不由 LLM 补写政策。
- 任务进行中的知识插问只回答知识，不中断任务（由 engine 层保证）。
"""

from __future__ import annotations

import asyncio

from edu_assistant.domain.message import BotMessage, UserMessage
from edu_assistant.domain.state import DialogueState
from edu_assistant.knowledge.intents import KNOWLEDGE_INTENTS
from edu_assistant.knowledge.provider import BaseProvider, KnowledgeMaterial
from edu_assistant.knowledge.responder import KnowledgeResponder
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


class KnowledgeHandler:
    def __init__(self, providers: dict[str, BaseProvider], responder: KnowledgeResponder) -> None:
        self.providers = providers
        self.responder = responder

    async def handle(
        self,
        intents: list[str],
        state: DialogueState,
        user_message: UserMessage,
    ) -> list[BotMessage]:
        # 保序去重
        seen: set[str] = set()
        unique_intents: list[str] = []
        for intent in intents:
            if intent in KNOWLEDGE_INTENTS and intent not in seen:
                seen.add(intent)
                unique_intents.append(intent)
        if not unique_intents:
            return [
                BotMessage(
                    text="抱歉，我没能理解你想咨询的内容。你可以问我课程与班次信息、退款政策、报名与开课政策、平台使用指南，也可以让我帮你查订单、查学习进度、申请退款或提交工单。"
                )
            ]

        query = (user_message.text or "").strip()
        focused = state.focused_object

        # 收集该意图需要的 Provider（保序去重）
        provider_names: list[str] = []
        for intent_id in unique_intents:
            for name in KNOWLEDGE_INTENTS[intent_id].providers:
                if name not in provider_names:
                    provider_names.append(name)

        tasks = []
        used_providers = []
        for name in provider_names:
            provider = self.providers.get(name)
            if provider is None:
                logger.warning("Provider 未注册: %s", name)
                continue
            used_providers.append(provider)
            tasks.append(provider.fetch(query, focused, state.sender_id))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        materials: list[KnowledgeMaterial] = []
        for provider, result in zip(used_providers, results):
            if isinstance(result, BaseException):
                logger.warning("Provider %s 检索失败: %s", provider.name, result)
                continue
            materials.extend(result)

        if not materials:
            answer = (
                "暂时没有查到与你问题直接相关的资料。为了不误导你，我不做猜测；\n"
                "你可以换个说法再问一次，或提交工单由人工客服为你确认。"
            )
        else:
            answer = await self.responder.respond(query, materials)

        messages = [BotMessage(text=answer)]
        # 任务进行中的知识插问：回复后附带当前任务提示（不压栈、不中断）
        active = state.tasks.active
        if active is not None:
            collected = [k for k, v in (active.slots or {}).items() if v and not k.startswith("_")]
            hint = f"我们刚才的「{active.flow_name}」还在继续"
            if collected:
                hint += f"（已收集：{ '、'.join(collected[:5]) }）"
            messages.append(BotMessage(text=f"{hint}，随时告诉我要继续、补充信息或取消。"))
        return messages
