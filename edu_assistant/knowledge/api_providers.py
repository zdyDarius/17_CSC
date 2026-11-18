"""业务 API Provider：课程搜索/详情/班次（course_info）、班次详情（cohort_info）。"""

from __future__ import annotations

from edu_assistant.domain.message import MessageObject
from edu_assistant.integrations.edu_data.client import EduDataClient
from edu_assistant.knowledge.provider import BaseProvider, KnowledgeMaterial
from edu_assistant.utils.errors import EduDataApiError, EduDataNetworkError
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

DELIVERY_MODE_LABELS = {
    "online_live": "线上直播",
    "online_recorded": "线上录播",
    "offline_face_to_face": "线下面授",
}

IDENTITY_LABELS = {
    "in_school_student": "在校生",
    "working_professional": "职场人",
    "job_seeker": "求职者",
    "freelancer": "自由职业者",
}


def extract_keyword(query: str, focused_object: MessageObject | None) -> str:
    """课程关键词：优先 series 对象标题，其次用户文本。"""
    if focused_object is not None and focused_object.type == "series" and focused_object.title:
        return focused_object.title
    if focused_object is not None and focused_object.type == "series":
        return str(focused_object.id)
    return (query or "").strip()


class SeriesApiProvider(BaseProvider):
    """api.series：课程搜索 + 课程详情 + 在售班次（组合查询，项目计划 二.7）。"""

    name = "api.series"

    def __init__(self, edu_client: EduDataClient) -> None:
        self.edu_client = edu_client

    async def fetch(
        self, query: str, focused_object: MessageObject | None, user_id: str
    ) -> list[KnowledgeMaterial]:
        keyword = extract_keyword(query, focused_object)
        if not keyword:
            return [
                KnowledgeMaterial(
                    source=self.name,
                    title="缺少课程关键词",
                    content="用户没有提供课程关键词，也无法从业务对象获取课程。需要向用户澄清想了解哪门课程（请用户说出课程名称，如「Python 全栈」）。",
                )
            ]
        try:
            search = await self.edu_client.search_series(keyword)
        except (EduDataApiError, EduDataNetworkError) as exc:
            logger.warning("课程搜索失败: %s", exc.message)
            return []
        rows = search.get("list", []) or []
        if not rows:
            return [
                KnowledgeMaterial(
                    source=self.name,
                    title="课程搜索",
                    content=f"未搜索到与「{keyword}」相关的在售课程。可建议用户提供更准确的课程名称或转人工咨询。",
                )
            ]
        # 取评分最高的一条，组合详情与班次
        best = rows[0]
        series_id = int(best["seriesId"])
        materials: list[KnowledgeMaterial] = []
        try:
            detail = await self.edu_client.get_series_detail(series_id)
            cohorts = await self.edu_client.list_series_cohorts(series_id)
        except (EduDataApiError, EduDataNetworkError) as exc:
            logger.warning("课程详情查询失败 series=%s: %s", series_id, exc.message)
            detail, cohorts = {}, []

        lines = [f"课程：{detail.get('seriesName', best.get('seriesName'))}"]
        if detail.get("description"):
            lines.append(f"课程介绍：{detail['description']}")
        if detail.get("targetLearnerIdentityCodes"):
            names = "、".join(
                IDENTITY_LABELS.get(c, c) for c in detail["targetLearnerIdentityCodes"]
            )
            lines.append(f"适用人群：{names}")
        lines.append(
            f"授课方式：{DELIVERY_MODE_LABELS.get(detail.get('deliveryModeCode', ''), detail.get('deliveryModeCode', '未知'))}"
        )
        if detail.get("avgScore"):
            lines.append(f"学员评分：{round(float(detail['avgScore']), 1)}（{detail.get('reviewCount', 0)} 条评价）")
        materials.append(
            KnowledgeMaterial(source=self.name, title=f"课程详情：{detail.get('seriesName', '')}", content="\n".join(lines))
        )
        if cohorts:
            cohort_lines = [f"「{detail.get('seriesName', '')}」在售班次："]
            for cohort in cohorts[:5]:
                price = f"￥{cohort.get('salePrice')}"
                date = cohort.get("startDate") or "随报随学"
                cohort_lines.append(
                    f"- {cohort.get('cohortName')}：{price}，开班 {date}"
                    + (f"，班主任 {cohort.get('headTeacherName')}" if cohort.get("headTeacherName") else "")
                    + f"（{cohort.get('currentStudentCount')}/{cohort.get('maxStudentCount')} 人）"
                )
            materials.append(
                KnowledgeMaterial(source=self.name, title="在售班次", content="\n".join(cohort_lines))
            )
        if len(rows) > 1:
            others = "、".join(r.get("seriesName", "") for r in rows[1:4])
            materials.append(
                KnowledgeMaterial(
                    source=self.name,
                    title="其他相关课程",
                    content=f"另有相关课程：{others}。如需了解某一门，请告诉我课程名。",
                )
            )
        return materials


class CohortApiProvider(BaseProvider):
    """api.cohort：班次详情（模块 + 课次安排）。需要 cohort_id 或 cohort 对象。"""

    name = "api.cohort"

    def __init__(self, edu_client: EduDataClient) -> None:
        self.edu_client = edu_client

    def _cohort_id(self, focused_object: MessageObject | None) -> int | None:
        if focused_object is None or focused_object.type != "cohort":
            return None
        try:
            return int(focused_object.id)
        except (TypeError, ValueError):
            return None

    async def fetch(
        self, query: str, focused_object: MessageObject | None, user_id: str
    ) -> list[KnowledgeMaterial]:
        cohort_id = self._cohort_id(focused_object)
        if cohort_id is None:
            return [
                KnowledgeMaterial(
                    source=self.name,
                    title="缺少班次",
                    content="缺少具体班次信息，需要向用户澄清想了解哪个班次（班次名称或从课程详情中选择班次）。",
                )
            ]
        try:
            detail = await self.edu_client.get_cohort_detail(cohort_id)
        except (EduDataApiError, EduDataNetworkError) as exc:
            logger.warning("班次详情查询失败 cohort=%s: %s", cohort_id, exc.message)
            return []
        lines = [
            f"班次：{detail.get('cohortName')}",
            f"学费：￥{detail.get('salePrice')}",
            f"开班：{detail.get('startDate') or '随报随学'}"
            + (f"，结班 {detail.get('endDate')}" if detail.get("endDate") else ""),
        ]
        if detail.get("headTeacherName"):
            lines.append(f"班主任：{detail['headTeacherName']}")
        modules = detail.get("modules") or []
        if modules:
            module_lines = [f"- {m.get('moduleName')}（{m.get('lessonCount')} 课时）" for m in modules[:8]]
            lines.append("课程模块：\n" + "\n".join(module_lines))
        sessions = detail.get("sessions") or []
        if sessions:
            session_lines = [
                f"- {s.get('teachingDate')} {s.get('sessionTitle')}（{s.get('startTime')}~{s.get('endTime')}）"
                for s in sessions[:10]
            ]
            lines.append("课次安排：\n" + "\n".join(session_lines))
        return [KnowledgeMaterial(source=self.name, title=f"班次详情：{detail.get('cohortName')}", content="\n".join(lines))]
