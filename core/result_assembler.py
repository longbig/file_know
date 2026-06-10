"""结果组装模块

职责：
- 将通过 LLM 语义判定的候选评论句组装为最终 AnalysisResult
- 从 Reference 映射填充被评文献完整字段
- 利用 LLM 返回的 evaluated_paper 信息增强字段
- 与现有 Pydantic 数据模型（CommentRecord / AnalysisResult）对接
"""

import logging
import re
from core.llm_analyzer import (
    AnalysisResult,
    CommentRecord,
    EvaluatedPaper,
    ReviewingPaper,
    JudgeResult,
)
from core.record_splitter import CandidateRecord
from core.ref_parser import Reference
from core.pdf_parser import PaperMetadata

logger = logging.getLogger(__name__)


def _clean_pdf_artifact(text: str) -> str:
    """清理 PDF 提取文本中的常见残留

    - 换行软连字符: "chemi- cal" → "chemical"（完全移除连字符）
    - 换行复合词: "water- in-salt" → "water-in-salt"（保留连字符，移除空格）
    - 非断行连字符 U+2011 → 普通连字符
    - 控制字符移除
    """
    if not text:
        return text
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    text = text.replace('\u2011', '-')   # 非断行连字符
    text = text.replace('\u00ad', '')     # 软连字符 (invisible soft hyphen)

    # 常见独立短词（不应被视为后缀）
    _standalone_shorts = {
        'in', 'on', 'at', 'to', 'by', 'up', 'or', 'an', 'as',
        'is', 'it', 'be', 'do', 'go', 'if', 'no', 'so', 'we',
        'he', 'of', 'ox', 'ion', 'and', 'the', 'for', 'but',
        'not', 'are', 'was', 'has', 'had', 'can', 'may', 'its',
    }

    def _fix_line_break_hyphen(m):
        left = m.group(1)
        right = m.group(2)

        # 右侧含连字符 → 复合词: "water- in-salt" → "water-in-salt"
        if '-' in right:
            return left + '-' + right

        right_lower = right.lower()
        # 右侧是短后缀（≤4字符、小写、非独立词）→ 软连字符: "chemi- cal" → "chemical"
        if (len(right) <= 4
                and right[0].islower()
                and right_lower not in _standalone_shorts):
            return left + right

        # 其余情况 → 复合词，保留连字符: "water- repellent" → "water-repellent"
        return left + '-' + right

    text = re.sub(r'(\w+)- (\w+)', _fix_line_break_hyphen, text)

    # 修复不匹配的智能引号：'Word" rest' → '"Word" rest'
    # PDF 解析可能丢失标题开头的左引号
    if '\u201d' in text and '\u201c' not in text:
        idx = text.index('\u201d')
        text = '\u201c' + text[:idx] + text[idx:]

    return text


def assemble_results(
    accepted_candidates: list[CandidateRecord],
    judge_results: list[JudgeResult],
    references: list[Reference],
    metadata: PaperMetadata,
) -> AnalysisResult:
    """将通过语义判定的候选句组装为最终分析结果

    Args:
        accepted_candidates: 通过 LLM 语义判定的候选评论句列表
        judge_results: 对应的 LLM 判定结果（含 evaluated_paper）
        references: 参考文献列表
        metadata: 施评文献元数据

    Returns:
        AnalysisResult 对象，与现有数据模型兼容
    """
    # 组装施评文献信息
    reviewing_paper = _build_reviewing_paper(metadata)

    # 组装评论句记录
    comment_records = []
    for i, candidate in enumerate(accepted_candidates):
        judge = judge_results[i] if i < len(judge_results) else None
        record = _build_comment_record(candidate, references, judge)
        comment_records.append(record)

    # 去重：按 (评论句原文规范化, 第一作者, 年份) 三元组去重
    seen = set()
    deduped = []
    for r in comment_records:
        key = (re.sub(r'\s+', ' ', r.评论句原文).strip(), r.被评文献.第一作者, r.被评文献.年份)
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    if len(deduped) < len(comment_records):
        logger.info(f"去重：{len(comment_records)} → {len(deduped)} 条")
    comment_records = deduped

    logger.info(f"结果组装完成：{len(comment_records)} 条评论句记录")

    return AnalysisResult(
        施评文献=reviewing_paper,
        评论句记录=comment_records,
    )


def _build_reviewing_paper(metadata: PaperMetadata) -> ReviewingPaper:
    """从 PaperMetadata 构建施评文献信息"""
    first_author = metadata.first_author
    is_chinese = any('\u4e00' <= c <= '\u9fff' for c in first_author) if first_author else False

    if is_chinese:
        all_authors = ", ".join(metadata.authors_cn) if metadata.authors_cn else ""
    else:
        all_authors = ", ".join(metadata.authors_en) if metadata.authors_en else ""

    if is_chinese:
        others = [a for a in (metadata.authors_cn or []) if a != first_author]
    else:
        others = [a for a in (metadata.authors_en or []) if a != first_author]
    other_authors = ", ".join(others)

    title = metadata.title_cn or metadata.title_en or ""
    journal = metadata.journal_cn or metadata.journal_en or ""
    institution = _clean_pdf_artifact(metadata.institution_cn or metadata.institution_en or "")
    country = metadata.country or ""

    return ReviewingPaper(
        全部作者=all_authors,
        第一作者=first_author,
        其他作者=other_authors,
        文章名=title,
        期刊名称=journal,
        年份=metadata.year or "",
        卷=metadata.volume or "",
        期=metadata.issue or "",
        起止页码=metadata.pages or "",
        第一作者机构=institution,
        第一作者国家=country,
    )


def _normalize_marker(marker: str) -> str:
    """标准化标志词输出

    多词标志词中，如果末尾词是常见动词/裸词（这些词单独不构成标志词），
    则剥离末尾词，只保留核心评价修饰语。

    例如：
    - "first used" → "first"
    - "first reported" → "first"
    - "originally proposed" → "originally"
    - "initially described" → "initially"
    - "pioneered" → "pioneered"（单词不处理）
    """
    if not marker or not marker.strip():
        return marker

    cleaned = marker.strip()
    words = cleaned.split()

    # 单词标志词不处理
    if len(words) <= 1:
        return cleaned

    # 可剥离的末尾动词/裸词（这些词单独不构成标志词）
    strippable_suffixes = {
        'reported', 'proposed', 'discovered', 'described', 'published',
        'demonstrated', 'suggested', 'provided', 'used', 'introduced',
        'developed', 'applied', 'identified', 'established', 'synthesized',
        'observed', 'detected', 'measured', 'presented', 'noted',
        'shown', 'found', 'studied', 'investigated', 'examined',
        'explored', 'revealed', 'confirmed', 'verified', 'documented',
    }

    # 如果最后一个词是可剥离的动词，去掉它
    if words[-1].lower() in strippable_suffixes:
        prefix = ' '.join(words[:-1]).strip()
        if prefix:
            return prefix

    return cleaned


def _clean_sentence_text(text: str) -> str:
    """清理评论句文本

    移除句子开头可能包含的编号小节标题前缀，
    例如 "3.1.1.1  "Water‑in‑Salt"/..." 在 sentence_splitter
    插入句子边界后被包含在评论句文本中。

    同时移除控制字符和 PDF 解析残留。
    """
    if not text:
        return text

    # 通用 PDF 文本清理
    text = _clean_pdf_artifact(text)

    # 移除开头的编号小节标题
    # 模式1：编号 + 双空格 + 标题 + 双空格 + 正文
    # 例如 "3.1.1.1  "Water-in-Salt"/...  A 'water-in-salt'..."
    section_prefix = re.match(
        r'^(\d+(?:\.\d+)+\.?)'         # group(1): 编号如 3.1.1.1
        r'(\s{2,})'                     # group(2): 编号后的双空格
        r'(.+?)'                        # group(3): 标题文本（非贪婪）
        r'(\s{2,})',                    # group(4): 标题后的双空格
        text,
    )

    if section_prefix:
        cleaned = text[section_prefix.end():]
        if cleaned and (cleaned[0].isupper() or cleaned[0] in '""\u201c\u201d'):
            return cleaned

    # 模式2：编号 + 空格 + 标题(含括号缩写) + 空格 + 正文
    # 例如 "3.1  Aqueous Rechargeable Lithium Batteries (ARLBs) Aqueous rechargeable..."
    # 标题以右括号结尾，后跟空格和大写字母开头的正文
    section_prefix2 = re.match(
        r'^(\d+(?:\.\d+)+\.?)'       # 编号
        r'\s+'                        # 编号后空格
        r'(.+?\))\s+'                # 标题（以右括号结尾）+ 空格
        r'(?=[A-Z"\u201c])',          # 正文以大写字母或引号开头
        text,
    )

    if section_prefix2:
        cleaned = text[section_prefix2.end():]
        if cleaned and len(section_prefix2.group(2)) < 120:
            return cleaned

    return text


def _build_comment_record(
    candidate: CandidateRecord,
    references: list[Reference],
    judge: JudgeResult | None = None,
) -> CommentRecord:
    """从候选记录构建评论句记录

    优先级：LLM evaluated_paper > 参考文献 Reference > 候选句基础信息

    Args:
        candidate: 候选评论句记录
        references: 参考文献列表
        judge: LLM 判定结果（含 evaluated_paper）

    Returns:
        CommentRecord 对象
    """
    ref = candidate.matched_ref
    evaluated = _build_evaluated_paper(candidate, ref, judge)

    return CommentRecord(
        评论句原文=_clean_sentence_text(candidate.sentence_text),
        标志词=_normalize_marker(candidate.marker),
        被评文献=evaluated,
    )


def _build_evaluated_paper(
    candidate: CandidateRecord,
    ref=None,
    judge: JudgeResult | None = None,
) -> EvaluatedPaper:
    """构建被评文献信息

    三级信息源合并（优先级调整）：
    1. 参考文献 Reference（最高优先级，精确的文献元数据）
    2. LLM 返回的 evaluated_paper（仅补充缺失字段，尤其是机构推断）
    3. 候选句基础信息（作者名/年份 — 兜底）

    Args:
        candidate: 候选评论句记录
        ref: 匹配到的参考文献对象
        judge: LLM 判定结果

    Returns:
        EvaluatedPaper 对象
    """
    # 基础信息（候选句中提取的）
    base = {
        "全部作者列表": [candidate.author_name],
        "第一作者": candidate.author_name,
        "其他作者": "",
        "文章名": "",
        "期刊名称": "",
        "年份": candidate.year,
        "卷": "",
        "期": "",
        "起止页码": "",
        "第一作者机构": "",
        "第一作者国家": "",
    }

    # LLM evaluated_paper 信息补充（低优先级，先写入）
    if judge and judge.evaluated_paper:
        ep = judge.evaluated_paper
        # 过滤掉 "et al." 等非作者项
        clean_authors = [a for a in (ep.全部作者列表 or [])
                         if a.strip().lower() not in ('et al.', 'et al', 'et.al.', 'et.al')]
        if clean_authors:
            base["全部作者列表"] = clean_authors
        if ep.第一作者:
            base["第一作者"] = ep.第一作者
        if ep.其他作者:
            base["其他作者"] = ep.其他作者
        if ep.文章名:
            base["文章名"] = ep.文章名
        if ep.期刊名称:
            base["期刊名称"] = ep.期刊名称
        if ep.年份:
            base["年份"] = ep.年份
        if ep.卷:
            base["卷"] = ep.卷
        if ep.期:
            base["期"] = ep.期
        if ep.起止页码:
            base["起止页码"] = ep.起止页码
        # 机构信息只有 LLM 能推断，始终接受
        if ep.第一作者机构:
            base["第一作者机构"] = ep.第一作者机构
        if ep.第一作者国家:
            base["第一作者国家"] = ep.第一作者国家

    # 参考文献信息覆盖（最高优先级 — 后写入覆盖 LLM）
    if ref is not None:
        if ref.authors:
            base["全部作者列表"] = ref.authors
        if ref.first_author:
            base["第一作者"] = ref.first_author
        if len(ref.authors) > 1:
            base["其他作者"] = ", ".join(ref.authors[1:])
        if ref.title:
            base["文章名"] = _clean_pdf_artifact(ref.title)
        if ref.journal:
            base["期刊名称"] = ref.journal
        if ref.year:
            base["年份"] = ref.year
        if ref.volume:
            base["卷"] = ref.volume
        if ref.issue:
            base["期"] = ref.issue
        if ref.pages:
            base["起止页码"] = ref.pages

    return EvaluatedPaper(**base)
