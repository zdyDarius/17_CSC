"""集中配置：pydantic-settings 读取 .env。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # edu_assistant/conf/config.py → 17_CSC


class Settings(BaseSettings):
    """客服系统全部配置项。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM（OpenAI 兼容端点）
    llm_model: str = "qwen-flash"
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_timeout_seconds: int = 30

    # 客服系统自有库
    database_url: str = "mysql+aiomysql://user:password@127.0.0.1:3306/edu_cs?charset=utf8mb4"

    # edu-data 业务服务 API
    edu_api_base_url: str = "http://127.0.0.1:8000"
    edu_api_timeout_seconds: float = 10.0
    edu_api_get_retries: int = 2

    # 只读订单明细适配器（业务库，仅 SELECT 白名单表）
    edu_readonly_db_url: str = (
        "mysql+aiomysql://user:password@127.0.0.1:3306/edu?charset=utf8mb4"
    )

    # 服务
    app_host: str = "0.0.0.0"
    app_port: int = 18082

    # 知识库
    faq_file: str = "knowledge_base/faq.json"
    docs_dir: str = "knowledge_base/documents"

    # 会话与安全约束
    max_text_length: int = 2000
    max_history_turns: int = 12
    max_paused_tasks: int = 5
    max_flow_steps_per_turn: int = 30

    @property
    def faq_path(self) -> Path:
        return PROJECT_ROOT / self.faq_file

    @property
    def docs_path(self) -> Path:
        return PROJECT_ROOT / self.docs_dir

    @property
    def flow_config_path(self) -> Path:
        return PROJECT_ROOT / "flow_config" / "user_flows.yml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
