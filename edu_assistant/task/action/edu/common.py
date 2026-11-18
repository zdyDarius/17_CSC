"""教育 Action 共用小工具。"""

from __future__ import annotations


def map_alias(raw: str, aliases: dict[str, str]) -> str | None:
    """别名归一：精确 → 小写精确 → 包含式匹配（别名是输入子串且枚举唯一）。"""
    mapped = aliases.get(raw) or aliases.get(raw.lower())
    if mapped:
        return mapped
    hits = {value for alias, value in aliases.items() if alias and alias in raw}
    return hits.pop() if len(hits) == 1 else None
