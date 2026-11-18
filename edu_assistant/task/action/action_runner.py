"""Action 执行入口：异常兜底，任何 Action 失败都有稳定错误码与可控回复。"""

from __future__ import annotations

from edu_assistant.integrations.edu_data.client import EduDataClient
from edu_assistant.integrations.edu_data.order_item_adapter import OrderItemAdapter
from edu_assistant.task.action.base import ActionContext, ActionResult
from edu_assistant.task.action.registry import get_action
from edu_assistant.utils.errors import (
    AppError,
    EduDataApiError,
    EduDataNetworkError,
)
from edu_assistant.utils.logging import get_logger

logger = get_logger(__name__)


class ActionRunner:
    def __init__(
        self,
        settings,
        edu_client: EduDataClient,
        order_item_adapter: OrderItemAdapter,
    ) -> None:
        self.settings = settings
        self.edu_client = edu_client
        self.order_item_adapter = order_item_adapter

    async def run(self, action_name: str, ctx: ActionContext) -> ActionResult:
        try:
            action = get_action(action_name)
            result = await action.run(ctx)
            if result is None:  # 所有分支必须返回结果，禁止 None
                return ActionResult()
            return result
        except EduDataApiError as exc:
            # 业务错误（4xx/409 等）：透传稳定错误码，不重试写请求
            logger.warning("action %s 业务失败: %s %s", action_name, exc.code, exc.message)
            return ActionResult(slots_update={"_action_error": exc.message, "_action_error_code": exc.code})
        except EduDataNetworkError as exc:
            logger.warning("action %s 网络失败: %s", action_name, exc.message)
            return ActionResult(slots_update={"_action_error": exc.message, "_action_error_code": exc.code})
        except AppError as exc:
            logger.warning("action %s 失败: %s %s", action_name, exc.code, exc.message)
            return ActionResult(slots_update={"_action_error": exc.message, "_action_error_code": exc.code})
        except Exception as exc:  # noqa: BLE001
            logger.exception("action %s 未捕获异常", action_name)
            return ActionResult(
                slots_update={
                    "_action_error": "系统处理异常，请稍后重试或转人工客服",
                    "_action_error_code": "ACTION_INTERNAL_ERROR",
                }
            )
