"""知识回复器：资料 → LLM 组织答案；LLM 失败时返回资料摘要降级。"""

from __future__ import annotations

from langchain_core.prompts import PromptTemplate

from edu_assistant.knowledge.provider import KnowledgeMaterial
from edu_assistant.prompts.loader import load_prompt
from edu_assistant.utils.llm import get_llm
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


class KnowledgeResponder:
    def _format_materials(self, materials: list[KnowledgeMaterial]) -> str:
        blocks = []
        for index, m in enumerate(materials, start=1):
            blocks.append(f"[{index}]（来源：{m.source}）{m.title}\n{m.content}")
        return "\n\n".join(blocks)

    async def respond(self, query: str, materials: list[KnowledgeMaterial]) -> str:
        materials_text = self._format_materials(materials)
        try:
            prompt = PromptTemplate.from_template(
                load_prompt("knowledge_respond"), template_format="jinja2"
            )
            chain = prompt | get_llm()
            result = await chain.ainvoke(
                input={"user_text": query, "materials_text": materials_text}
            )
            content = (getattr(result, "content", "") or "").strip()
            if content:
                return content
        except Exception as exc:  # noqa: BLE001
            logger.warning("知识回复模型失败，降级返回资料摘要: %s", exc)
        return self._fallback_summary(materials)

    def _fallback_summary(self, materials: list[KnowledgeMaterial]) -> str:
        """LLM 失败降级：直接返回检索资料摘要（不伪造业务结果）。"""
        if not materials:
            return (
                "暂时没有查到与你的问题直接相关的资料，模型服务也暂时不可用。\n"
                "你可以稍后再试，或提交工单由人工客服跟进。"
            )
        lines = ["根据检索到的资料："]
        for m in materials[:3]:
            lines.append(f"【{m.title}】\n{m.content}")
        lines.append("（模型服务暂时不可用，以上为检索资料原文摘要，供你参考。）")
        return "\n\n".join(lines)
