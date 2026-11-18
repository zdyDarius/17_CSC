"""FastAPI 应用：lifespan 装配、统一异常、健康检查、演示页。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from edu_assistant.api.chat_router import router as chat_router
from edu_assistant.api.dependencies import init_engine_components
from edu_assistant.api.session_router import router as session_router
from edu_assistant.conf.config import get_settings
from edu_assistant.repository.orm.models import init_tables
from edu_assistant.utils.db import close_db_engine, init_db_engine
from edu_assistant.utils.errors import AppError
from edu_assistant.utils.httpx_client import close_http_client, init_http_client
from edu_assistant.utils.llm import init_llm
from edu_assistant.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    settings = get_settings()
    # 1 数据库 / HTTP client / LLM
    init_db_engine()
    init_http_client()
    init_llm()
    # 2 客服库表（幂等）
    await init_tables()
    # 3 组件装配（流程目录只加载一次；只读适配器启动）
    components = init_engine_components()
    await components.order_item_adapter.start()
    logger.info("edu-customer-service 启动完成 port=%s", settings.app_port)
    yield
    # 释放
    await components.order_item_adapter.stop()
    await close_http_client()
    await close_db_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="Edu Customer Service", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # 不泄露堆栈/SQL/密钥
        logger.exception("未处理异常")
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "message": "系统内部错误，请稍后重试", "data": None},
        )

    app.include_router(session_router)
    app.include_router(chat_router)

    @app.get("/health")
    async def health() -> dict:
        return {"code": 0, "message": "ok", "data": {"status": "ok"}}

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
