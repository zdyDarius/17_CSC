"""提示词模板加载（启动时缓存，不每轮读盘）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "jinja2"


@lru_cache
def load_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.j2"
    return path.read_text(encoding="utf-8")
