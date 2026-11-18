"""客服系统自有库：异步 SQLAlchemy 引擎（lifespan 初始化/释放）。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from edu_assistant.conf.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        return
    _engine = create_async_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=5,
        max_overflow=5,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def close_db_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("数据库会话工厂未初始化，请通过 lifespan 启动")
    return _session_factory
