"""大模型分析模块

职责：
- 调用 Claude API（通过中转站）分析论文全文
- 提取学术评论句
- JSON Schema 校验 + 自动重试
"""

import json
import logging
import re

import httpx
from pydantic import BaseModel, field_validator

from config import LLMConfig
from core.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)


# ── Pydantic 数据模型 ──────────────────────────────────────────────

class EvaluatedPaper(BaseModel):
    """被评文献信息"""
    全部作者列表: list[str] = []
    第一作者: str = ""
    其他作者: str = ""
    文章名: str = ""
    期刊名称: str = ""
    年份: str = ""
    卷: str = ""
    期: str = ""
    起止页码: str = ""
    第一作者机构: str = ""
    第一作者国家: str = ""


class CommentRecord(BaseModel):
    """单条评论句记录"""
    评论句原文: str
    标志词: str
    被评文献: EvaluatedPaper

    @field_validator("评论句原文")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("评论句原文不能为空")
        return v


class AnalysisResult(BaseModel):
    """分析结果"""
    评论句记录: list[CommentRecord] = []


# ── API 调用 ──────────────────────────────────────────────────────

def _clean_json_response(text: str) -> str:
    """清理大模型返回的文本，提取 JSON 部分"""
    text = text.strip()

    # 移除可能的 markdown 代码块标记
    if text.startswith("```"):
        # 找到第一个换行后开始
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1:]
        # 移除末尾的 ```
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # 尝试找到 JSON 对象
    brace_start = text.find("{")
    if brace_start > 0:
        text = text[brace_start:]

    # 找到最后一个 }
    brace_end = text.rfind("}")
    if brace_end != -1 and brace_end < len(text) - 1:
        text = text[:brace_end + 1]

    return text


def call_llm(
    full_text: str,
    authors: str,
    config: LLMConfig,
    progress_callback=None,
) -> AnalysisResult:
    """调用大模型分析论文

    Args:
        full_text: 论文全文
        authors: 施评文献作者列表字符串
        config: LLM 配置
        progress_callback: 进度回调函数

    Returns:
        AnalysisResult 包含评论句记录列表
    """
    user_prompt = USER_PROMPT_TEMPLATE.format(
        authors=authors,
        full_text=full_text,
    )

    last_error = None
    for attempt in range(1, config.max_retries + 1):
        if progress_callback:
            progress_callback(f"正在调用大模型分析（第 {attempt} 次尝试）...")

        try:
            response_text = _api_call(config, user_prompt)
            cleaned = _clean_json_response(response_text)
            result = AnalysisResult.model_validate_json(cleaned)
            logger.info(f"成功解析到 {len(result.评论句记录)} 条评论句记录")
            return result

        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"第 {attempt} 次尝试：JSON 解析失败 - {e}")
        except Exception as e:
            last_error = e
            logger.warning(f"第 {attempt} 次尝试：失败 - {e}")

    logger.error(f"所有 {config.max_retries} 次尝试均失败")
    raise RuntimeError(
        f"大模型分析失败（已重试 {config.max_retries} 次）：{last_error}"
    )


def _api_call(config: LLMConfig, user_prompt: str) -> str:
    """调用 Claude API（兼容 OpenAI 格式的中转站）"""
    # 中转站通常使用 OpenAI 兼容的 /v1/chat/completions 接口
    url = f"{config.base_url.rstrip('/')}/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config.model,
        "temperature": config.temperature,
        "max_tokens": 8192,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    with httpx.Client(timeout=120.0) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    # 提取回复文本
    content = data["choices"][0]["message"]["content"]
    return content
