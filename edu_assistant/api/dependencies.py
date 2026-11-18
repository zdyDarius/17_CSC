"""FastAPI 应用与依赖注入（lifespan 装配）。"""

from __future__ import annotations

from fastapi import Depends

from edu_assistant.engine.builder import build_engine, EngineComponents
from edu_assistant.service.dialogue_service import DialogueService

_components: EngineComponents | None = None
_service: DialogueService | None = None


def init_engine_components() -> EngineComponents:
    global _components, _service
    if _components is None:
        _components = build_engine()
        _service = DialogueService(_components)
    return _components


def get_dialogue_service() -> DialogueService:
    if _service is None:
        init_engine_components()
    assert _service is not None
    return _service
