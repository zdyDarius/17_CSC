"""知识意图定义（项目计划 5.1）。

订单与学习进度统一由任务流程处理，不设置重复的 order_info/learning_summary 知识意图。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KnowledgeIntent:
    id: str
    description: str
    providers: list[str]  # 保序 Provider 列表
    requires_object: bool = False  # 是否需要业务对象/关键词


KNOWLEDGE_INTENTS: dict[str, KnowledgeIntent] = {
    "course_info": KnowledgeIntent(
        id="course_info",
        description="课程内容、适用人群、班次安排、价格等课程信息咨询。需要课程关键词（文本）或课程对象。",
        providers=["api.series"],
        requires_object=True,
    ),
    "cohort_info": KnowledgeIntent(
        id="cohort_info",
        description="具体班次的模块、课次安排、授课教师、开班时间等信息。需要班次 ID 或班次对象。",
        providers=["api.cohort"],
        requires_object=True,
    ),
    "refund_policy": KnowledgeIntent(
        id="refund_policy",
        description="退款条件、可退金额、处理时限等退款政策咨询。",
        providers=["faq.edu", "rag.edu"],
    ),
    "enroll_policy": KnowledgeIntent(
        id="enroll_policy",
        description="报名流程、开班时间、换班/延期、听课证明等政策咨询。",
        providers=["faq.edu", "rag.edu"],
    ),
    "platform_guide": KnowledgeIntent(
        id="platform_guide",
        description="平台功能使用、学习中心、作业考试操作等使用指南。",
        providers=["rag.edu"],
    ),
}


def intents_catalog() -> dict[str, dict]:
    """给规划器的意图目录。"""
    return {
        i.id: {
            "id": i.id,
            "description": i.description,
            "requires_object": i.requires_object,
        }
        for i in KNOWLEDGE_INTENTS.values()
    }
