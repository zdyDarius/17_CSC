"""服务入口：python main.py"""

from __future__ import annotations

import uvicorn

from edu_assistant.conf.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "edu_assistant.api.app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
