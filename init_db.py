"""初始化客服库与表：python init_db.py

自动创建客服库（CREATE DATABASE IF NOT EXISTS）与 edu_assistant 自有的 5 张表（幂等）。
不触碰 edu-data 业务库（edu）；业务库的数据初始化见 edu-data 项目的 README。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from edu_assistant.conf.config import get_settings
from edu_assistant.repository.orm.models import init_tables
from edu_assistant.utils.db import close_db_engine, init_db_engine
from edu_assistant.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


async def ensure_database() -> None:
    """不存在则创建客服库。"""
    url = make_url(get_settings().database_url)
    database = url.database
    # 注意：URL.set(database=None) 在 SQLAlchemy 中表示"保持不变"，无法清除库名；
    # 必须用 _replace 才能真正置空，否则连接时仍会请求 edu_cs 导致 1049。
    server_url = url._replace(database=None)
    engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(
            text(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        )
    await engine.dispose()
    logger.info("客服库就绪: %s", database)


async def main() -> None:
    setup_logging()
    await ensure_database()
    init_db_engine()
    await init_tables()
    logger.info("客服库表初始化完成")
    await close_db_engine()


if __name__ == "__main__":
    asyncio.run(main())
