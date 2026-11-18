"""LLM 接入：OpenAI 兼容端点（DashScope compatible-mode）。

lifespan 启动时初始化一次；测试中可注入假模型。
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

_llm: BaseChatModel | None = None


def init_llm() -> None:
    global _llm
    if _llm is not None:
        return
    # 延迟导入，避免不使用 LLM 的工具脚本引入 langchain
    from langchain.chat_models import init_chat_model

    from edu_assistant.conf.config import get_settings

    settings = get_settings()
    _llm = init_chat_model(
        model=settings.llm_model,
        model_provider="openai",
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=settings.llm_timeout_seconds,
        temperature=0,
    )


def get_llm() -> BaseChatModel:
    if _llm is None:
        init_llm()
    assert _llm is not None
    return _llm
