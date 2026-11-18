"""开发辅助：以 edu-data 项目为工作目录启动其 app.main（供本地功能测试用）。"""

from __future__ import annotations

import os
import runpy
import sys

EDU_DATA_DIR = (
    "/Users/zdy/Desktop/尚硅谷大模型项目实战之掌柜小二实战/edu-data"
)

if __name__ == "__main__":
    os.chdir(EDU_DATA_DIR)
    sys.path.insert(0, EDU_DATA_DIR)
    sys.argv = ["app.main"]
    runpy.run_module("app.main", run_name="__main__")
