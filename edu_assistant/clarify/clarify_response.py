"""可控澄清：非法计划/对象不匹配/歧义 → 澄清问题，不产生 500。"""

from __future__ import annotations

from langchain_core.prompts import PromptTemplate

from edu_assistant.domain.message import BotMessage, UserMessage
from edu_assistant.domain.state import DialogueState
from edu_assistant.prompts.loader import load_prompt
from edu_assistant.prompts.models import ClarifyReason
from edu_assistant.utils.llm import get_llm
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

REASON_LABELS: dict[ClarifyReason, str] = {
    ClarifyReason.MISSING_TRACK: "无法判断用户想做什么",
    ClarifyReason.MULTIPLE_TRACKS: "用户一句话里包含多个互相冲突的请求",
    ClarifyReason.MISSING_TASK_COMMANDS: "识别到任务意图但缺少具体指令",
    ClarifyReason.MISSING_KNOWLEDGE_INTENT: "缺少知识意图",
    ClarifyReason.MISSING_FOCUSED_OBJECT: "缺少业务对象",
    ClarifyReason.OBJECT_REQUIRES_INTENT: "用户发送了业务对象但当前没有进行中的任务",
    ClarifyReason.OBJECT_TYPE_MISMATCH: "业务对象类型不匹配",
    ClarifyReason.INVALID_TASK_COMMAND: "任务指令不合法",
    ClarifyReason.UNKNOWN_KNOWLEDGE_INTENT: "知识意图不在支持范围内",
    ClarifyReason.PLAN_FAILED: "意图识别服务暂时不可用",
    ClarifyReason.TOO_MANY_PAUSED_TASKS: "暂停任务过多，需要先取消或恢复一个任务",
    ClarifyReason.AMBIGUOUS_INPUT: "输入模糊",
}


class ClarifyResponse:
    async def response(
        self,
        reason: ClarifyReason,
        state: DialogueState,
        user_message: UserMessage,
    ) -> list[BotMessage]:
        label = REASON_LABELS.get(reason, str(reason.value))
        active = state.tasks.active
        active_task_text = ""
        if active is not None:
            active_task_text = f"{active.flow_name}（步骤 {active.step_id}）"
        focused_text = ""
        if state.focused_object is not None:
            focused_text = f"{state.focused_object.type}（{state.focused_object.title or state.focused_object.id}）"

        try:
            prompt = PromptTemplate.from_template(
                load_prompt("clarify_respond"), template_format="jinja2"
            )
            chain = prompt | get_llm()
            result = await chain.ainvoke(
                input={
                    "reason": label,
                    "user_text": (user_message.text or user_message.short_text())[:500],
                    "active_task_text": active_task_text,
                    "focused_object_text": focused_text,
                    "object_type": (
                        state.focused_object.type if state.focused_object else ""
                    ),
                }
            )
            content = (getattr(result, "content", "") or "").strip()
            if content:
                return [BotMessage(text=content)]
        except Exception as exc:  # noqa: BLE001
            logger.warning("澄清回复模型失败，使用兜底文案: %s", exc)
        return [BotMessage(text=self._fallback(reason, active))]

    def _fallback(self, reason: ClarifyReason, active) -> str:
        if reason == ClarifyReason.OBJECT_REQUIRES_INTENT:
            return (
                "收到你发送的对象。请告诉我你想对它做什么，例如：查订单状态、申请退款或提交工单。"
            )
        if reason == ClarifyReason.TOO_MANY_PAUSED_TASKS and active is not None:
            return (
                f"你当前有多个暂停的任务。请先告诉我「继续」或「取消」当前的「{active.flow_name}」，再开始新的任务。"
            )
        return (
            "抱歉，我没完全理解你的意思。我可以帮你：查询订单状态、查询学习进度、申请退款、提交工单，"
            "或介绍课程/班次、退款政策、报名政策与平台使用指南。请告诉我你想做哪一项？"
        )
