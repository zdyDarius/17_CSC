"""规划器：用户消息 + 有限上下文 → TurnPlan JSON。

- 信息范围最小化：只接收有限历史、当前任务摘要、流程/意图目录。
- LLM 输出非法 JSON 时重试一次；再失败进入可控澄清（PLAN_FAILED）。
"""

from __future__ import annotations

import json
from dataclasses import asdict

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate

from edu_assistant.domain.message import UserMessage
from edu_assistant.domain.state import DialogueState
from edu_assistant.prompts.history_builder import (
    build_history_text,
    build_task_state_summary,
    render_user_message,
)
from edu_assistant.prompts.loader import load_prompt
from edu_assistant.prompts.models import TurnPlan
from edu_assistant.task.flow.models import FlowCatalog
from edu_assistant.utils.llm import get_llm
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


class PlanError(Exception):
    """规划失败（LLM 输出无法解析）。"""


class TurnPlanner:
    def __init__(self, knowledge_intents: dict[str, dict]) -> None:
        # knowledge_intents: {id: {"id":..., "description":..., "requires_object": bool}}
        self.knowledge_intents = knowledge_intents
        self._chain = None

    def _get_chain(self):
        if self._chain is None:
            prompt = PromptTemplate.from_template(
                load_prompt("turn_plan"), template_format="jinja2"
            )
            self._chain = prompt | get_llm() | JsonOutputParser()
        return self._chain

    async def plan(
        self, state: DialogueState, user_message: UserMessage, catalog: FlowCatalog
    ) -> TurnPlan:
        flows_json = json.dumps(
            [flow.summary() for flow in catalog.flows.values()],
            ensure_ascii=False,
        )
        intents_json = json.dumps(
            list(self.knowledge_intents.values()), ensure_ascii=False
        )
        task_state_json = json.dumps(
            build_task_state_summary(state), ensure_ascii=False, default=str
        )
        focused_object_json = (
            json.dumps(asdict(state.focused_object), ensure_ascii=False)
            if state.focused_object is not None
            else "null"
        )

        payload = {
            "user_message": render_user_message(user_message),
            "conversation_history": build_history_text(state),
            "focused_object_json": focused_object_json,
            "flows_json": flows_json,
            "knowledge_intents_json": intents_json,
            "task_state_json": task_state_json,
        }

        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                result = await self._get_chain().ainvoke(input=payload)
                return TurnPlan.from_dict(result if isinstance(result, dict) else None)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("规划失败 attempt=%s: %s", attempt, exc)
        raise PlanError(f"LLM 规划输出无法解析: {last_error}") from last_error
