"""全局配置管理"""
import os
from dataclasses import dataclass, field


@dataclass
class LLMConfig:
    """大模型连接配置"""
    api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    base_url: str = os.getenv("ANTHROPIC_BASE_URL", "https://timesniper.club")
    model: str = "claude-sonnet-4-6"
    available_models: list[str] = field(default_factory=lambda: [
        "claude-sonnet-4-6",
        "claude-opus-4-6",
    ])
    max_retries: int = 3
    temperature: float = 0.0


@dataclass
class AppConfig:
    """应用配置"""
    output_dir: str = "output"
    llm: LLMConfig = field(default_factory=LLMConfig)
