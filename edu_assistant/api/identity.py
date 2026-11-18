"""API 身份校验：X-User-Id 请求头解析（各路由共用）。"""

from __future__ import annotations

from fastapi import Header
from typing import Annotated

from edu_assistant.utils.errors import unauthorized

UserIdHeader = Annotated[str | None, Header(alias="X-User-Id")]


def require_user_id(x_user_id: str | None) -> str:
    """X-User-Id 必填且为数字（存在性由 edu-data API 校验）。"""
    if not x_user_id or not x_user_id.strip():
        raise unauthorized("MISSING_USER_ID", "缺少请求头 X-User-Id")
    value = x_user_id.strip()
    if not value.isdigit():
        raise unauthorized("INVALID_USER_ID", "X-User-Id 必须为数字用户 ID")
    return value
