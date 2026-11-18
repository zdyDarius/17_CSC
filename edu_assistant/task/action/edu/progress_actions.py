"""教育业务 Action：学习进度查询。"""

from __future__ import annotations

from edu_assistant.task.action.base import ActionContext, ActionResult
from edu_assistant.task.action.registry import register_action
from edu_assistant.utils.errors import EduDataApiError, EduDataNetworkError
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)

ENROLL_STATUS_LABELS = {
    "active": "在读",
    "completed": "已完成",
    "cancelled": "已取消",
    "refunded": "已退费",
}

EXAM_KEYWORDS = ("考试", "分数", "成绩", "考了多少")

EXAM_STATUS_LABELS = {
    "not_started": "未开始",
    "absent": "缺考",
    "submitted": "已提交",
    "graded": "已批改",
    "pending": "待参加",
}


@register_action("action_select_cohort")
class SelectCohortAction:
    """多候选班次时：解析用户选择（序号或名称）；不匹配时清空重问。"""

    async def run(self, ctx: ActionContext) -> ActionResult:
        slots = ctx.slots
        choices = slots.get("cohort_choices") or []
        if not choices:
            return ActionResult(clear_slots=["cohort_choice"])
        choice = str(slots.get("cohort_choice", "")).strip()
        if not choice:
            return ActionResult()

        matched = None
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(choices):
                matched = choices[index]
        else:
            hits = [c for c in choices if choice.lower() in str(c.get("cohort_name", "")).lower()]
            if len(hits) == 1:
                matched = hits[0]
        if matched is None:
            return ActionResult(
                clear_slots=["cohort_choice"],
                slots_update={"cohort_choice_invalid": "未能识别你的选择，请回复班次对应的序号（如 1 或 2）。"},
            )
        return ActionResult(
            slots_update={
                "cohort_id": int(matched["cohort_id"]),
                "cohort_name": str(matched.get("cohort_name", "")),
            },
            clear_slots=["cohort_choice", "cohort_choices", "cohort_choices_text", "cohort_choice_invalid"],
        )


@register_action("action_prefill_cohort")
class PrefillCohortAction:
    """「考试分数」等追问：用户消息含考试关键词且本会话刚查过班次时，从会话缓存预填班次。

    计划 4.2 的考试明细追问路径：机器人邀请的「考试分数」视为对最近查询班次的明确指代。
    其他情况为无操作（流程照常收集班次名）。
    """

    async def run(self, ctx: ActionContext) -> ActionResult:
        slots = ctx.slots
        if slots.get("cohort_id") or slots.get("cohort_name"):
            return ActionResult()  # 已有班次（用户消息直接给了）
        user_text = (ctx.user_message.text or "") if ctx.user_message else ""
        if not any(kw in user_text for kw in EXAM_KEYWORDS):
            return ActionResult()
        cached = ctx.state.session_cache.last_cohort or {}
        if not cached.get("cohort_id"):
            return ActionResult()
        return ActionResult(
            slots_update={
                "cohort_id": int(cached["cohort_id"]),
                "cohort_name": str(cached.get("cohort_name", "")),
            },
            clear_slots=["cohort_choice", "cohort_choices", "cohort_choices_text"],
        )


@register_action("action_resolve_cohort")
class ResolveCohortAction:
    """学习进度查询流程：班次名 → cohort_id → 进度汇总。

    - 精确匹配优先；模糊匹配多个候选时写入候选让用户选择。
    - 用户消息包含考试关键词时，追加查询考试分数明细。
    """

    async def run(self, ctx: ActionContext) -> ActionResult:
        slots = ctx.slots
        if slots.get("cohort_id"):
            cohort_id = int(slots["cohort_id"])
            cohort_name = str(slots.get("cohort_name", ""))
        else:
            cohort_name = str(slots.get("cohort_name", "")).strip()
            if not cohort_name:
                return ActionResult(clear_slots=["cohort_name"])
            try:
                result = await ctx.edu_client.list_my_cohorts(ctx.user_id)
            except (EduDataApiError, EduDataNetworkError) as exc:
                return ActionResult(slots_update={"resolve_failed": exc.message})

            rows = result.get("list", []) or []
            exact = [r for r in rows if str(r.get("cohortName", "")).strip() == cohort_name]
            fuzzy = [
                r
                for r in rows
                if cohort_name and cohort_name in str(r.get("cohortName", ""))
            ] or [
                r
                for r in rows
                if str(r.get("seriesName", "")) and cohort_name in str(r.get("seriesName", ""))
            ]
            if exact:
                target = exact[0]
            elif len(fuzzy) == 1:
                target = fuzzy[0]
            elif len(fuzzy) > 1:
                lines = [
                    f"{i}. {r.get('cohortName')}（{r.get('seriesName')}，{ENROLL_STATUS_LABELS.get(r.get('enrollStatusCode', ''), r.get('enrollStatusCode'))}）"
                    for i, r in enumerate(fuzzy, start=1)
                ]
                return ActionResult(
                    slots_update={
                        "cohort_choices": [
                            {"cohort_id": int(r["cohortId"]), "cohort_name": str(r.get("cohortName"))}
                            for r in fuzzy
                        ],
                        "cohort_choices_text": "\n".join(lines),
                    }
                )
            else:
                return ActionResult(
                    slots_update={
                        "resolve_failed": f"没有找到你在读的班次「{cohort_name}」，请确认班次名称或告诉我你报名的课程名。"
                    }
                )
            cohort_id = int(target["cohortId"])
            cohort_name = str(target.get("cohortName", ""))

        try:
            progress = await ctx.edu_client.get_cohort_progress(ctx.user_id, cohort_id)
        except (EduDataApiError, EduDataNetworkError) as exc:
            return ActionResult(slots_update={"resolve_failed": exc.message})

        attendance = progress.get("attendance") or {}
        video = progress.get("video") or {}
        homework = progress.get("homework") or {}
        exam = progress.get("exam") or {}
        enroll_status = ENROLL_STATUS_LABELS.get(
            progress.get("enrollStatusCode", ""), progress.get("enrollStatusCode", "")
        )

        lines = [
            f"你在「{cohort_name}」（{enroll_status}）的学习进度：",
            f"- 考勤：出勤 {attendance.get('presentCount', 0)}/{attendance.get('totalSessions', 0)} 次，缺勤 {attendance.get('absentCount', 0)} 次",
            f"- 视频：完成 {video.get('completedVideos', 0)}/{video.get('totalVideos', 0)} 个，累计观看 {video.get('watchedSeconds', 0)} 秒",
            f"- 作业：提交 {homework.get('submittedCount', 0)}/{homework.get('totalHomeworks', 0)} 次，已批改 {homework.get('correctedCount', 0)} 次",
            f"- 考试：参加 {exam.get('submittedCount', 0)}/{exam.get('totalExams', 0)} 场，缺考 {exam.get('absentCount', 0)} 场",
        ]

        # 用户追问考试分数时再查明细（避免默认增加请求）
        user_text = (ctx.user_message.text or "") if ctx.user_message else ""
        if any(kw in user_text for kw in EXAM_KEYWORDS):
            try:
                exams = await ctx.edu_client.list_my_exams(ctx.user_id, cohort_id)
            except (EduDataApiError, EduDataNetworkError):
                exams = []
            if exams:
                exam_lines = []
                for ex in exams:
                    status_code = str(ex.get("attemptStatus", "not_started"))
                    status = EXAM_STATUS_LABELS.get(status_code, status_code)
                    score = ex.get("scoreValue")
                    score_text = f"，得分 {score}" if score not in (None, "", "0.00") else ""
                    exam_lines.append(f"  · {ex.get('examName')}（{status}{score_text}）")
                lines.append("- 考试明细：\n" + "\n".join(exam_lines))
            else:
                lines.append("- 考试明细：暂无可查询的考试记录")
            lines.append("如需了解其他学习情况，随时告诉我。")
        else:
            lines.append("如需查看考试分数明细，请告诉我「考试分数」。")

        # 会话级缓存最近查询的班次（供「考试分数」等机器人邀请的追问复用，计划 4.2）
        ctx.state.session_cache.last_cohort = {
            "cohort_id": cohort_id,
            "cohort_name": cohort_name,
        }
        return ActionResult(
            slots_update={
                "cohort_id": cohort_id,
                "cohort_name": cohort_name,
                "progress_summary": "\n".join(lines),
            },
            clear_slots=["cohort_choices", "cohort_choices_text"],
        )