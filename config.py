"""全局配置管理"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# ── models.json 路径 ──────────────────────────────────────────────
MODELS_JSON_PATH = Path(__file__).parent / "models.json"


def _load_models_config() -> dict:
    """加载 models.json 配置文件"""
    if not MODELS_JSON_PATH.exists():
        return {"providers": [], "default_model": "claude-sonnet-4-6"}
    with open(MODELS_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_all_models() -> list[str]:
    """获取所有可用模型名称列表（按 provider 顺序排列）"""
    config = _load_models_config()
    models = []
    for provider in config.get("providers", []):
        for model in provider.get("models", []):
            if model not in models:
                models.append(model)
    return models


def get_default_model() -> str:
    """获取默认模型名"""
    config = _load_models_config()
    return config.get("default_model", "claude-sonnet-4-6")


def get_model_provider(model_name: str) -> dict | None:
    """根据模型名查找对应的 provider 配置

    Returns:
        {"name", "base_url", "api_key", "auth_type", "max_tokens_field", "extra_payload"} 或 None
    """
    config = _load_models_config()
    for provider in config.get("providers", []):
        if model_name in provider.get("models", []):
            return {
                "name": provider["name"],
                "base_url": provider["base_url"],
                "api_key": provider["api_key"],
                "auth_type": provider.get("auth_type", "bearer"),
                "max_tokens_field": provider.get("max_tokens_field", "max_tokens"),
                "extra_payload": provider.get("extra_payload", {}),
            }
    return None


@dataclass
class LLMConfig:
    """大模型连接配置"""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    auth_type: str = "bearer"           # "bearer" | "api-key"
    max_tokens_field: str = "max_tokens" # "max_tokens" | "max_completion_tokens"
    extra_payload: dict = field(default_factory=dict)  # 供应商特有的额外请求参数
    max_retries: int = 3
    temperature: float = 0.0

    def __post_init__(self):
        """如果未指定 model / api_key / base_url，则从 models.json 自动填充"""
        if not self.model:
            self.model = get_default_model()

        # 根据模型名查找 provider，补全所有字段
        provider = get_model_provider(self.model)
        if provider:
            if not self.api_key:
                self.api_key = provider["api_key"]
            if not self.base_url:
                self.base_url = provider["base_url"]
            if self.auth_type == "bearer":
                self.auth_type = provider.get("auth_type", "bearer")
            if self.max_tokens_field == "max_tokens":
                self.max_tokens_field = provider.get("max_tokens_field", "max_tokens")
            if not self.extra_payload:
                self.extra_payload = provider.get("extra_payload", {})

        # 最终兜底：环境变量
        if not self.api_key:
            self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not self.base_url:
            self.base_url = os.getenv("ANTHROPIC_BASE_URL", "https://timesniper.club")


@dataclass
class AppConfig:
    """应用配置"""
    output_dir: str = "output"
    llm: LLMConfig = field(default_factory=LLMConfig)
