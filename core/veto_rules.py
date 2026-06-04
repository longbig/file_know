"""一票否决规则模块

职责：
- 实现 9 条一票否决规则中可由确定性逻辑判定的 6 条
- 语义层面的 2 条（标志词描述操作步骤、三要素不属同一事件）留给 LLM
- 被评对象是机构而非作者（需语义判断）也留给 LLM
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 词表加载 ──────────────────────────────────────────────────────

_MARKERS_DATA: dict | None = None


def _load_markers_data() -> dict:
    """懒加载 markers.json"""
    global _MARKERS_DATA
    if _MARKERS_DATA is None:
        markers_path = Path(__file__).parent.parent / "markers.json"
        with open(markers_path, "r", encoding="utf-8") as f:
            _MARKERS_DATA = json.load(f)
    return _MARKERS_DATA


def _get_self_citation_words() -> tuple[list[str], list[str]]:
    """获取自引检测词表"""
    data = _load_markers_data()
    return data.get("self_citation_en", []), data.get("self_citation_cn", [])


def _get_bare_words() -> list[str]:
    """获取裸词列表"""
    data = _load_markers_data()
    return data.get("bare_words", [])


# ── 否决规则判定结果 ──────────────────────────────────────────────

@dataclass
class VetoResult:
    """否决规则判定结果"""
    vetoed: bool          # 是否被否决
    rule_id: int          # 触发的规则编号（1-9），0 表示未触发
    reason: str           # 否决理由


def pass_result() -> VetoResult:
    """通过（未被否决）"""
    return VetoResult(vetoed=False, rule_id=0, reason="")


# ── 规则 1：作者+年份仅在括号内 ──────────────────────────────────

def check_bracket_only(
    text: str,
    author_mentions: list,  # list[AuthorMention]
    year_mentions: list,    # list[YearMention]
) -> VetoResult:
    """规则 1：作者+年份仅在括号内出现，且括号外无作者/年份任一要素，剔除

    Args:
        text: 句子原文
        author_mentions: 句中提取的作者提及列表
        year_mentions: 句中提取的年份提及列表

    Returns:
        VetoResult
    """
    if not author_mentions or not year_mentions:
        return pass_result()

    # 检查是否所有作者都在括号内
    all_authors_in_bracket = all(a.in_bracket for a in author_mentions)

    # 检查是否所有年份都在括号内（排除 is_ref_number 类型）
    real_years = [y for y in year_mentions if not getattr(y, 'is_ref_number', False)]
    if not real_years:
        # 只有文献编号说明类年份，不适用此规则
        return pass_result()
    all_years_in_bracket = all(y.in_bracket for y in real_years)

    if all_authors_in_bracket and all_years_in_bracket:
        return VetoResult(
            vetoed=True,
            rule_id=1,
            reason="作者+年份仅在括号内出现，括号外无作者/年份要素",
        )

    # 补充：Author (Year) 格式 — 作者在括号外但年份全部在括号内
    # 合法：Smith (2010) first proposed...  → 作者是主语（句首或主句动作发出者）
    # 非法：as revealed by Liem (1988) / given a task by Lehner et al. (2011) → 作者在介词宾语位置
    if all_years_in_bracket:
        outside_authors = [a for a in author_mentions if not a.in_bracket]
        if outside_authors:
            # 方式1：author_extractor 已识别为 by_author_pattern（直接标记）
            # 方式2：向前搜索更大窗口内是否有 by/as 介词（覆盖中间插入词的情况）
            prep_pattern = re.compile(r'\b(?:by|as)\b', re.IGNORECASE)
            all_after_prep = all(
                getattr(a, 'preceded_by_prep', False) or
                bool(prep_pattern.search(text[max(0, a.position - 60):a.position]))
                for a in outside_authors
            )
            if all_after_prep:
                # 豁免：被动语态评论句，如 "was first reported by Author (Year)"
                # 检查 by 前方 80 字符内是否有非裸词标志词
                _marker_pattern = re.compile(
                    r'\b(?:first|firstly|initially|originally|earliest|novel(?:ly)?|'
                    r'pioneered?|introduced|developed|proposed|established|'
                    r'first\s+reported|first\s+described|first\s+proposed|'
                    r'first\s+demonstrated|first\s+identified|first\s+shown)\b',
                    re.IGNORECASE,
                )
                has_marker_before_by = any(
                    bool(_marker_pattern.search(text[max(0, a.position - 80):a.position]))
                    for a in outside_authors
                    if getattr(a, 'preceded_by_prep', False) or
                       bool(prep_pattern.search(text[max(0, a.position - 60):a.position]))
                )
                if has_marker_before_by:
                    return pass_result()
                return VetoResult(
                    vetoed=True,
                    rule_id=1,
                    reason="Author(Year) 引用格式：年份均在括号内，作者位于介词宾语位置",
                )

    return pass_result()


# ── 规则 2：自引检测 ──────────────────────────────────────────────

def check_self_citation(
    text: str,
    author_mentions: list,  # list[AuthorMention]
    self_authors: set[str],  # 施评文献作者集合（标准化后的名称）
) -> VetoResult:
    """规则 2：被评文献作者与施评文献作者重叠（自引），剔除

    检测两个维度：
    1. 自引词表匹配（our group, we, 本课题组 等）
    2. 作者名与施评文献作者重叠

    Args:
        text: 句子原文
        author_mentions: 句中提取的作者提及列表
        self_authors: 施评文献作者名称集合

    Returns:
        VetoResult
    """
    text_lower = text.lower()

    # 维度 1：自引词表匹配
    en_words, cn_words = _get_self_citation_words()

    for word in en_words:
        # 英文自引词用词边界匹配
        pattern = r'\b' + re.escape(word) + r'\b'
        if re.search(pattern, text_lower):
            return VetoResult(
                vetoed=True,
                rule_id=2,
                reason=f"自引检测：句中出现自引表述 \"{word}\"",
            )

    for word in cn_words:
        if word in text:
            return VetoResult(
                vetoed=True,
                rule_id=2,
                reason=f"自引检测：句中出现自引表述 \"{word}\"",
            )

    # 维度 2：作者名重叠检测
    if self_authors and author_mentions:
        for mention in author_mentions:
            mention_name = mention.name.strip()
            mention_lower = mention_name.lower()
            for self_author in self_authors:
                self_name = self_author.strip()
                self_lower = self_name.lower()
                if not self_lower or not mention_lower:
                    continue

                # 对于被评作者只有姓氏（无名字、短于5个字符）的情况：
                # 姓氏匹配可能是假阳性（如 Wang 是极常见的姓氏），
                # normalize_authors() 会把全名中的姓氏也提取到集合中，
                # 导致 "Wang"(被评) vs "Wang"(从 Jing Wang 提取) 误匹配。
                # 因此短姓氏一律不在规则引擎中否决，留给 LLM 语义判断。
                if len(mention_lower) < 5 and ' ' not in mention_lower:
                    continue

                # 完全匹配（不区分大小写）— 仅对有全名的被评作者
                if mention_lower == self_lower:
                    return VetoResult(
                        vetoed=True,
                        rule_id=2,
                        reason=f"自引检测：被评作者 \"{mention.name}\" 与施评文献作者 \"{self_author}\" 完全匹配",
                    )

                # 对于有全名的被评作者（含空格或较长）：双向子串匹配
                if mention_lower in self_lower or self_lower in mention_lower:
                    return VetoResult(
                        vetoed=True,
                        rule_id=2,
                        reason=f"自引检测：被评作者 \"{mention.name}\" 与施评文献作者 \"{self_author}\" 重叠",
                    )

    return pass_result()


# ── 规则 3：非期刊文献（由 ref_parser 判断） ────────────────────

def check_non_journal(
    matched_ref,  # Reference | None
) -> VetoResult:
    """规则 3：被评文献非期刊论文，剔除

    Args:
        matched_ref: 匹配到的参考文献对象，None 表示未匹配到

    Returns:
        VetoResult
    """
    if matched_ref is None:
        # 未匹配到参考文献，不否决（保留待人工审核）
        return pass_result()

    if not matched_ref.is_journal:
        return VetoResult(
            vetoed=True,
            rule_id=3,
            reason=f"非期刊文献：参考文献[{matched_ref.index}]类型为[{matched_ref.ref_type}]",
        )

    return pass_result()


# ── 规则 7：年份不匹配 ──────────────────────────────────────────

def check_year_mismatch(
    year_mentions: list,   # list[YearMention]
    matched_ref,           # Reference | None
) -> VetoResult:
    """规则 7：参考文献年份与句中年份不匹配（差1年也不算），剔除

    Args:
        year_mentions: 句中提取的年份列表
        matched_ref: 匹配到的参考文献对象

    Returns:
        VetoResult
    """
    if matched_ref is None or not matched_ref.year:
        return pass_result()

    # 提取句中所有精确年份（排除年代词和文献编号说明）
    exact_years = set()
    for ym in year_mentions:
        if not getattr(ym, 'is_decade', False) and not getattr(ym, 'is_ref_number', False):
            # 提取 4 位数字年份
            year_str = ym.year.strip()
            if re.match(r'^\d{4}$', year_str):
                exact_years.add(year_str)

    if not exact_years:
        return pass_result()

    # 检查是否有任何年份与参考文献匹配
    ref_year = matched_ref.year.strip()
    if ref_year in exact_years:
        return pass_result()

    return VetoResult(
        vetoed=True,
        rule_id=7,
        reason=f"年份不匹配：句中年份{exact_years}，参考文献年份{ref_year}",
    )


# ── 规则 8：仅标注参考文献编号，无年份/作者信息 ────────────────

def check_ref_number_only(
    text: str,
    author_mentions: list,  # list[AuthorMention]
    year_mentions: list,    # list[YearMention]
) -> VetoResult:
    """规则 8：句中仅有参考文献数字编号，无年份/作者信息，剔除

    Args:
        text: 句子原文
        author_mentions: 句中提取的作者提及列表
        year_mentions: 句中提取的年份提及列表

    Returns:
        VetoResult
    """
    # 如果有作者名和年份，不触发此规则
    if author_mentions and year_mentions:
        return pass_result()

    # 检查句中是否只有引用编号
    # 模式：[数字] 或 [数字,数字] 或 [数字-数字]
    ref_number_pattern = r'\[\d+(?:\s*[,，\-]\s*\d+)*\]'
    has_ref_numbers = bool(re.search(ref_number_pattern, text))

    if has_ref_numbers and (not author_mentions or not year_mentions):
        # 特殊情况：中文"在文【7,8】中"视为有年份
        cn_ref_pattern = r'[在文][【\[]\d+[,，\s]*\d*[】\]]'
        if re.search(cn_ref_pattern, text):
            # 已有对应的 YearMention (is_ref_number=True)
            # 但仍需要作者
            if not author_mentions:
                return VetoResult(
                    vetoed=True,
                    rule_id=8,
                    reason="仅有文献编号说明，无作者信息",
                )
            return pass_result()

        missing = []
        if not author_mentions:
            missing.append("作者")
        if not year_mentions:
            missing.append("年份")

        return VetoResult(
            vetoed=True,
            rule_id=8,
            reason=f"仅有参考文献编号，缺少{'和'.join(missing)}信息",
        )

    return pass_result()


# ── 规则 9：裸词禁用 ──────────────────────────────────────────────

def check_bare_word(
    marker_matches: list,  # list[MarkerMatch]
) -> VetoResult:
    """规则 9：标志词全为裸词（reported/proposed 等单独出现），剔除

    bare_words 列表中的词单独使用不构成标志词，
    必须与 first/firstly/originally/initially 等组合才有效。

    如果句中所有匹配到的标志词都是裸词，则否决。
    如果有至少一个非裸词标志词，则通过。

    Args:
        marker_matches: 标志词匹配结果列表

    Returns:
        VetoResult
    """
    if not marker_matches:
        return pass_result()

    # 检查是否所有标志词都是裸词
    all_bare = all(getattr(m, 'is_bare_word', False) for m in marker_matches)

    if all_bare:
        bare_words = [m.marker for m in marker_matches]
        return VetoResult(
            vetoed=True,
            rule_id=9,
            reason=f"裸词禁用：{', '.join(bare_words)} 单独出现不构成标志词",
        )

    return pass_result()


# ── 综合否决检查 ──────────────────────────────────────────────────

def apply_veto_rules(
    text: str,
    author_mentions: list,
    year_mentions: list,
    marker_matches: list,
    self_authors: set[str],
    matched_ref=None,
) -> VetoResult:
    """依次执行所有确定性否决规则

    执行顺序：规则2（自引）→ 规则1（括号）→ 规则9（裸词）→
    规则8（仅编号）→ 规则3（非期刊）→ 规则7（年份不匹配）

    规则4（标志词描述操作步骤）、规则5（三要素不属同一事件）、
    规则6（被评对象是机构非作者）需要语义判断，由 LLM 处理。

    Args:
        text: 句子原文
        author_mentions: 作者提及列表
        year_mentions: 年份提及列表
        marker_matches: 标志词匹配列表
        self_authors: 施评文献作者集合
        matched_ref: 匹配到的参考文献（可选）

    Returns:
        VetoResult - 第一个触发的否决规则结果，或通过
    """
    checks = [
        # 优先检查自引（最常见的否决原因之一）
        lambda: check_self_citation(text, author_mentions, self_authors),
        # 括号规则
        lambda: check_bracket_only(text, author_mentions, year_mentions),
        # 裸词禁用
        lambda: check_bare_word(marker_matches),
        # 仅编号无作者/年份
        lambda: check_ref_number_only(text, author_mentions, year_mentions),
        # 非期刊过滤
        lambda: check_non_journal(matched_ref),
        # 年份不匹配
        lambda: check_year_mismatch(year_mentions, matched_ref),
    ]

    for check in checks:
        result = check()
        if result.vetoed:
            logger.debug(f"否决规则 #{result.rule_id}: {result.reason}")
            return result

    return pass_result()
