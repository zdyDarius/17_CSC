"""统一异常与稳定错误码。

所有对外错误使用稳定错误码，异常响应不泄露堆栈、SQL 或密钥。
"""

from __future__ import annotations


class AppError(Exception):
    """客服系统统一异常基类。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def bad_request(code: str, message: str) -> AppError:
    return AppError(code, message, 400)


def unauthorized(code: str, message: str) -> AppError:
    return AppError(code, message, 401)


def not_found(code: str, message: str) -> AppError:
    return AppError(code, message, 404)


class EduDataApiError(AppError):
    """edu-data 业务 API 返回的业务错误（透传上游错误码）。"""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(code, message, status_code)


class EduDataNetworkError(AppError):
    """edu-data 网络/超时错误（结果未知）。"""

    def __init__(self, message: str = "业务服务暂时不可用") -> None:
        super().__init__("EDU_API_UNAVAILABLE", message, 503)


class FlowDefinitionError(AppError):
    """流程 YAML 定义非法（启动期校验失败）。"""

    def __init__(self, message: str) -> None:
        super().__init__("FLOW_DEFINITION_INVALID", message, 500)


class FlowInfiniteLoopError(AppError):
    """流程执行超过单轮最大步数，判定为死循环。"""

    def __init__(self, flow_id: str) -> None:
        super().__init__(
            "FLOW_INFINITE_LOOP",
            f"流程 {flow_id} 执行异常，已停止推进",
            500,
        )
