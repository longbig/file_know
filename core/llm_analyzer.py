"""大模型分析模块

职责：
- 调用 LLM API（通过中转站）分析论文全文（旧架构）
- 批量语义判定候选评论句（新架构）
- JSON Schema 校验 + 自动重试
"""

import json
import logging
import re

import httpx
from pydantic import BaseModel, field_validator

from config import LLMConfig
from core.prompts import (
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    SEMANTIC_JUDGE_PROMPT,
    JUDGE_USER_TEMPLATE,
    VERIFY_PROMPT,
    VERIFY_USER_TEMPLATE,
    format_candidate_for_judge,
)

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


# ── 新架构：批量语义判定 ─────────────────────────────────────────

class JudgeEvaluatedPaper(BaseModel):
    """LLM 语义判定返回的被评文献信息"""
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


class JudgeResult(BaseModel):
    """单条候选句的语义判定结果"""
    id: int
    accept: bool
    reason: str = ""
    evaluated_paper: JudgeEvaluatedPaper | None = None


class JudgeResponse(BaseModel):
    """LLM 语义判定的完整响应"""
    results: list[JudgeResult] = []


def judge_candidates(
    candidates: list,  # list[CandidateRecord]
    config: LLMConfig,
    self_authors_str: str = "",
    batch_size: int = 20,
    progress_callback=None,
) -> list[JudgeResult]:
    """批量语义判定候选评论句

    将候选句分批发送给 LLM，由 LLM 做语义层面的最终判定。
    每批约 20 条，避免单次请求过大。

    Args:
        candidates: 候选评论句列表（CandidateRecord）
        config: LLM 配置
        self_authors_str: 施评文献作者列表字符串（用于 LLM 判断自引）
        batch_size: 每批发送的候选句数量
        progress_callback: 进度回调

    Returns:
        所有候选句的判定结果列表
    """
    if not candidates:
        return []

    all_results = []

    # 分批处理
    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start:batch_start + batch_size]
        batch_end = min(batch_start + batch_size, len(candidates))

        if progress_callback:
            progress_callback(
                f"语义判定中... ({batch_start + 1}-{batch_end}/{len(candidates)})"
            )

        # 格式化候选句文本
        candidate_texts = []
        for i, c in enumerate(batch):
            # 构建参考文献信息
            ref_info = ""
            if c.matched_ref:
                ref = c.matched_ref
                ref_info = f"[{ref.index}] {ref.raw_text[:200]}"

            text = format_candidate_for_judge(
                candidate_id=batch_start + i + 1,
                sentence_text=c.sentence_text,
                marker=c.marker,
                author_name=c.author_name,
                year=c.year,
                prev_sentence=c.prev_sentence,
                next_sentence=c.next_sentence,
                ref_info=ref_info,
            )
            candidate_texts.append(text)

        user_prompt = JUDGE_USER_TEMPLATE.format(
            count=len(batch),
            self_authors=self_authors_str or "未知",
            candidates_text="\n\n".join(candidate_texts),
        )

        try:
            response_text = _api_call_with_system(
                config, SEMANTIC_JUDGE_PROMPT, user_prompt
            )
            cleaned = _clean_json_response(response_text)
            judge_resp = JudgeResponse.model_validate_json(cleaned)

            # 补全缺失的结果（LLM 可能遗漏某些候选句）
            result_map = {r.id: r for r in judge_resp.results}
            for i, c in enumerate(batch):
                cid = batch_start + i + 1
                if cid in result_map:
                    all_results.append(result_map[cid])
                else:
                    # LLM 未返回的候选句默认 accept（保守策略，避免漏提）
                    logger.warning(f"LLM 未返回候选句 #{cid} 的判定结果，默认 accept")
                    all_results.append(JudgeResult(id=cid, accept=True, reason="LLM 未返回结果"))

        except Exception as e:
            logger.error(f"语义判定批次 {batch_start + 1}-{batch_end} 失败: {e}")
            # 批次失败时全部默认 accept（保守策略）
            for i in range(len(batch)):
                cid = batch_start + i + 1
                all_results.append(
                    JudgeResult(id=cid, accept=True, reason=f"判定失败: {e}")
                )

    accepted = sum(1 for r in all_results if r.accept)
    rejected = sum(1 for r in all_results if not r.accept)
    logger.info(f"语义判定完成: {accepted} 条通过, {rejected} 条否决")

    return all_results


def _api_call_with_system(
    config: LLMConfig, system_prompt: str, user_prompt: str
) -> str:
    """调用大模型 API（可自定义 system prompt）"""
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
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    # 合并供应商特有的额外参数
    if config.extra_payload:
        payload.update(config.extra_payload)

    with httpx.Client(timeout=300.0) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    content = data["choices"][0]["message"]["content"]

    usage = data.get("usage", {})
    if usage:
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
        logger.info(f"[语义判定] Token 用量: 输入={input_tokens:,}, 输出={output_tokens:,}, "
                     f"合计={total_tokens:,}")

    return content


def verify_records(
    records: list[CommentRecord],
    full_text: str,
    config: LLMConfig,
    batch_size: int = 10,
    progress_callback=None,
) -> list[CommentRecord]:
    """对已组装的评论句做原文复检，过滤掉在原文中找不到的记录"""
    if not records:
        return records

    context = full_text[:8000]
    verified_records = []

    for batch_start in range(0, len(records), batch_size):
        batch = records[batch_start:batch_start + batch_size]
        if progress_callback:
            progress_callback(f"复检中... ({batch_start + 1}-{min(batch_start + batch_size, len(records))}/{len(records)})")

        candidates_text = "\n".join(
            f"[{batch_start + i + 1}] {r.评论句原文[:300]}"
            for i, r in enumerate(batch)
        )
        user_prompt = VERIFY_USER_TEMPLATE.format(
            count=len(batch),
            context=context,
            candidates_text=candidates_text,
        )

        try:
            response_text = _api_call_with_system(config, VERIFY_PROMPT, user_prompt)
            cleaned = _clean_json_response(response_text)
            data = json.loads(cleaned)
            result_map = {r["id"]: r.get("verified", True) for r in data.get("results", [])}
            for i, record in enumerate(batch):
                cid = batch_start + i + 1
                if result_map.get(cid, True):
                    verified_records.append(record)
                else:
                    reason = next((r.get("reason", "") for r in data.get("results", []) if r["id"] == cid), "")
                    logger.info(f"复检剔除 #{cid}: {reason}")
        except Exception as e:
            logger.warning(f"复检批次 {batch_start + 1} 失败，保留全部: {e}")
            verified_records.extend(batch)

    removed = len(records) - len(verified_records)
    if removed > 0:
        logger.info(f"复检完成：剔除 {removed} 条，保留 {len(verified_records)} 条")
    return verified_records
