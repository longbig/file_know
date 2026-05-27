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

class ReviewingPaper(BaseModel):
    """施评文献信息（本篇论文自身的元数据）"""
    全部作者: str = ""
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
    施评文献: ReviewingPaper = ReviewingPaper()
    评论句记录: list[CommentRecord] = []


# ── API 调用 ──────────────────────────────────────────────────────

def _clean_json_response(text: str) -> str:
    """清理大模型返回的文本，提取第一个完整的 JSON 对象

    使用大括号配对匹配，避免 LLM 返回多个 JSON 对象时
    把它们拼在一起导致 trailing characters 解析错误。
    """
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

    # 找到第一个 { 的位置
    brace_start = text.find("{")
    if brace_start == -1:
        return text

    # 大括号配对匹配，提取第一个完整的 JSON 对象
    depth = 0
    in_string = False
    escape_next = False
    for i in range(brace_start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            if in_string:
                escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start:i + 1]

    # 配对失败，回退到旧逻辑：第一个 { 到最后一个 }
    brace_end = text.rfind("}")
    if brace_end != -1:
        return text[brace_start:brace_end + 1]

    return text[brace_start:]


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

    if progress_callback:
        progress_callback("正在调用大模型分析...")

    try:
        response_text = _api_call(config, user_prompt)
        cleaned = _clean_json_response(response_text)
        result = AnalysisResult.model_validate_json(cleaned)
        logger.info(f"成功解析到 {len(result.评论句记录)} 条评论句记录")
        return result
    except Exception as e:
        logger.error(f"大模型分析失败: {e}")
        raise RuntimeError(f"大模型分析失败：{e}")


def _api_call(config: LLMConfig, user_prompt: str) -> str:
    """调用大模型 API（兼容 OpenAI 格式）"""
    url = f"{config.base_url.rstrip('/')}/v1/chat/completions"

    # 根据 auth_type 选择认证头
    if config.auth_type == "api-key":
        headers = {
            "api-key": config.api_key,
            "Content-Type": "application/json",
        }
    else:
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }

    payload = {
        "model": config.model,
        "temperature": config.temperature,
        config.max_tokens_field: 16384,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    # 合并供应商特有的额外参数（来自 models.json 的 extra_payload）
    if config.extra_payload:
        payload.update(config.extra_payload)

    with httpx.Client(timeout=300.0) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    # 提取回复文本
    content = data["choices"][0]["message"]["content"]

    # 记录 token 用量
    usage = data.get("usage", {})
    if usage:
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
        logger.info(f"Token 用量: 输入={input_tokens:,}, 输出={output_tokens:,}, "
                     f"合计={total_tokens:,}")

    return content
