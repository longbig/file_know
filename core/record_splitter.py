"""记录拆分模块

职责：
- 处理 independently 两作者两文献拆分
- 一句话评论多篇参考文献的拆分
- 每篇符合要求的参考文献各生成一条独立记录
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CandidateRecord:
    """候选评论句记录（规则引擎输出）"""
    sentence_text: str          # 句子原文
    sentence_index: int         # 句子序号
    marker: str                 # 匹配到的标志词
    author_name: str            # 被评作者名
    year: str                   # 被评年份
    ref_index: int = -1         # 匹配的参考文献编号（-1表示未匹配）
    matched_ref: object = None  # 匹配到的 Reference 对象
    other_refs_info: str = ""   # 同一评论句中其他被评文献信息
    prev_sentence: str = ""     # 前一句上下文
    next_sentence: str = ""     # 后一句上下文
    marker_matches: list = field(default_factory=list)  # 所有匹配到的标志词
    author_mentions: list = field(default_factory=list)  # 所有作者提及
    year_mentions: list = field(default_factory=list)    # 所有年份提及


def split_independently(
    sentence_text: str,
    marker: str,
    author_mentions: list,  # list[AuthorMention]
    year_mentions: list,    # list[YearMention]
    matched_refs: list,     # list[Reference]
    sentence_index: int = 0,
    prev_sentence: str = "",
    next_sentence: str = "",
    marker_matches: list = None,
) -> list[CandidateRecord] | None:
    """检测并处理 independently 拆分

    规则：句子中出现 independently 且有两位作者对应两篇参考文献，
    拆分为两条独立记录。

    Args:
        sentence_text: 句子原文
        marker: 主标志词
        author_mentions: 作者提及列表
        year_mentions: 年份提及列表
        matched_refs: 匹配到的参考文献列表
        sentence_index: 句子序号
        prev_sentence: 前一句
        next_sentence: 后一句
        marker_matches: 所有标志词匹配

    Returns:
        拆分后的记录列表，如果不需要拆分返回 None
    """
    # 检查是否包含 independently
    if not re.search(r'\bindependently\b', sentence_text, re.IGNORECASE):
        return None

    # 需要至少两个作者和两个匹配的参考文献
    if len(author_mentions) < 2 or len(matched_refs) < 2:
        return None

    logger.debug(f"检测到 independently 拆分场景：{len(author_mentions)} 位作者")

    records = []
    # 将作者与年份配对
    # 尝试按位置顺序配对
    used_refs = set()

    for author in author_mentions:
        # 为每个作者找最佳匹配的参考文献
        best_ref = None
        for ref in matched_refs:
            if id(ref) in used_refs:
                continue
            # 检查作者名是否匹配
            author_name_lower = author.name.lower()
            ref_author_lower = ref.first_author.lower() if ref.first_author else ""

            if (author_name_lower in ref_author_lower or
                    ref_author_lower in author_name_lower):
                best_ref = ref
                break

        if best_ref is None:
            continue

        used_refs.add(id(best_ref))

        # 构建其他文献信息
        other_info_parts = []
        for other_ref in matched_refs:
            if id(other_ref) != id(best_ref):
                other_info_parts.append(
                    f"{other_ref.first_author}, {other_ref.year}, {other_ref.journal}"
                )

        record = CandidateRecord(
            sentence_text=sentence_text,
            sentence_index=sentence_index,
            marker=marker,
            author_name=author.name,
            year=best_ref.year,
            ref_index=best_ref.index,
            matched_ref=best_ref,
            other_refs_info="; ".join(other_info_parts),
            prev_sentence=prev_sentence,
            next_sentence=next_sentence,
            marker_matches=marker_matches or [],
            author_mentions=[author],
            year_mentions=year_mentions,
        )
        records.append(record)

    if len(records) >= 2:
        logger.info(f"independently 拆分：1句 → {len(records)} 条记录")
        return records

    return None


def split_multiple_refs(
    sentence_text: str,
    marker: str,
    author_mentions: list,  # list[AuthorMention]
    year_mentions: list,    # list[YearMention]
    matched_refs: list,     # list[Reference]
    sentence_index: int = 0,
    prev_sentence: str = "",
    next_sentence: str = "",
    marker_matches: list = None,
) -> list[CandidateRecord]:
    """处理一句话评论多篇参考文献的拆分

    每篇符合要求的参考文献各生成一条独立记录，
    其余参考文献填入 other_refs_info 字段。

    Args:
        sentence_text: 句子原文
        marker: 主标志词
        author_mentions: 作者提及列表
        year_mentions: 年份提及列表
        matched_refs: 匹配到的参考文献列表（已过滤非期刊）
        sentence_index: 句子序号
        prev_sentence: 前一句
        next_sentence: 后一句
        marker_matches: 所有标志词匹配

    Returns:
        拆分后的记录列表（至少1条）
    """
    if not matched_refs:
        # 没有匹配到参考文献，仍生成记录但不填充参考文献信息
        if author_mentions and year_mentions:
            return [CandidateRecord(
                sentence_text=sentence_text,
                sentence_index=sentence_index,
                marker=marker,
                author_name=author_mentions[0].name,
                year=_best_year_for_author(author_mentions[0], year_mentions),
                prev_sentence=prev_sentence,
                next_sentence=next_sentence,
                marker_matches=marker_matches or [],
                author_mentions=author_mentions,
                year_mentions=year_mentions,
            )]
        return []

    if len(matched_refs) == 1:
        # 只有一篇参考文献，直接生成一条记录
        ref = matched_refs[0]
        return [CandidateRecord(
            sentence_text=sentence_text,
            sentence_index=sentence_index,
            marker=marker,
            author_name=_find_author_for_ref(ref, author_mentions),
            year=ref.year,
            ref_index=ref.index,
            matched_ref=ref,
            prev_sentence=prev_sentence,
            next_sentence=next_sentence,
            marker_matches=marker_matches or [],
            author_mentions=author_mentions,
            year_mentions=year_mentions,
        )]

    # 多篇参考文献：每篇各一条记录
    records = []
    for i, ref in enumerate(matched_refs):
        # 构建其他文献信息
        other_info_parts = []
        for j, other_ref in enumerate(matched_refs):
            if j != i:
                other_info_parts.append(
                    f"{other_ref.first_author}, {other_ref.year}, {other_ref.journal}"
                )

        record = CandidateRecord(
            sentence_text=sentence_text,
            sentence_index=sentence_index,
            marker=marker,
            author_name=_find_author_for_ref(ref, author_mentions),
            year=ref.year,
            ref_index=ref.index,
            matched_ref=ref,
            other_refs_info="; ".join(other_info_parts),
            prev_sentence=prev_sentence,
            next_sentence=next_sentence,
            marker_matches=marker_matches or [],
            author_mentions=author_mentions,
            year_mentions=year_mentions,
        )
        records.append(record)

    if len(records) > 1:
        logger.info(f"多文献拆分：1句 → {len(records)} 条记录")

    return records


def _find_author_for_ref(ref, author_mentions: list) -> str:
    """为参考文献找到对应的句中作者名

    Args:
        ref: Reference 对象
        author_mentions: 句中的作者提及列表

    Returns:
        最佳匹配的作者名，未找到则返回参考文献的 first_author
    """
    if not author_mentions:
        return ref.first_author if ref.first_author else ""

    ref_author = ref.first_author.lower() if ref.first_author else ""
    if not ref_author:
        return author_mentions[0].name

    for mention in author_mentions:
        mention_lower = mention.name.lower()
        if mention_lower in ref_author or ref_author in mention_lower:
            return mention.name

    # 未找到精确匹配，返回第一个作者
    return author_mentions[0].name


def _best_year_for_author(author_mention, year_mentions: list) -> str:
    """为作者找到最近位置的年份

    Args:
        author_mention: AuthorMention 对象
        year_mentions: 年份提及列表

    Returns:
        最近的年份字符串
    """
    if not year_mentions:
        return ""

    author_pos = author_mention.position
    best_year = year_mentions[0]
    best_dist = abs(author_pos - year_mentions[0].position)

    for ym in year_mentions[1:]:
        dist = abs(author_pos - ym.position)
        if dist < best_dist:
            best_dist = dist
            best_year = ym

    return best_year.year
