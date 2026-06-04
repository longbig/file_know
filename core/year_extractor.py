"""年份提取模块

职责：
- 从单个句子中提取所有年份提及
- 支持精确年份、年代词、时间定位词、中文文献编号说明
- 区分括号内/外年份
- 过滤非年份数字（度量单位、数学表达式等）
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── 常量定义 ──────────────────────────────────────────────────────

# 年份有效范围
_YEAR_MIN = 1800
_YEAR_MAX = 2099

# 年份前方的排除字符（出现在数字紧前方则不是年份）
_EXCLUDE_PREFIX_PATTERN = re.compile(r'[=±~><≈≤≥×÷]$')

# 年份后方紧跟的度量单位（出现则不是年份）
# 注意：不使用 IGNORECASE，因为单位符号大小写敏感（A=安培 vs a=普通字母）
_UNIT_PATTERN = re.compile(
    r'^[\s\-]?(?:'
    r'°[CFK]|℃|℉'                        # 温度
    r'|[nμµm]?m\b|cm\b|km\b'              # 长度（加 \b 防止匹配 "many"等词）
    r'|[nμµm]?L\b|mL\b|µL\b'              # 体积
    r'|[nμµm]?g\b|mg\b|kg\b'              # 质量
    r'|[kMGT]?Hz\b'                        # 频率
    r'|[mkμµ]?V\b'                         # 电压
    r'|[mkμµ]?A\b'                         # 电流（大写A）
    r'|[mkμµ]?W\b'                         # 功率（大写W）
    r'|[kMG]?eV\b|keV\b|MeV\b|GeV\b'     # 能量
    r'|[mkμµ]?Pa\b|hPa\b|kPa\b|MPa\b'   # 压强
    r'|mol\b|rpm\b|ppm\b|ppb\b'           # 其他
    r'|[%‰]'                               # 百分比
    r')'
)

# ── 数据结构 ──────────────────────────────────────────────────────


@dataclass
class YearMention:
    """年份提及信息"""
    year: str             # 年份字符串，如 "2020"、"1950s"；文献编号说明时为 "ref"
    position: int         # 在句中的起始字符位置
    in_bracket: bool      # 是否在括号内
    is_decade: bool       # 是否为年代词，如 "the 1950s"
    is_ref_number: bool   # 是否为文献编号说明（如"在文【7,8】中"）


# ── 内部工具函数 ──────────────────────────────────────────────────

def _build_bracket_map(text: str) -> list[tuple[int, int]]:
    """构建括号起止位置映射

    支持多种括号：()、（）、[]、【】

    Returns:
        括号区间列表 [(start, end), ...]
    """
    # 定义括号对
    open_chars = {'(', '（', '[', '【'}
    close_map = {
        '(': ')', '（': '）',
        '[': ']', '【': '】',
    }

    brackets: list[tuple[int, int]] = []
    stack: list[tuple[str, int]] = []  # (开括号字符, 位置)

    for i, ch in enumerate(text):
        if ch in open_chars:
            stack.append((ch, i))
        elif stack:
            opener, start = stack[-1]
            expected_close = close_map.get(opener)
            if ch == expected_close:
                stack.pop()
                brackets.append((start, i))

    return brackets


def _is_in_bracket(pos: int, bracket_map: list[tuple[int, int]]) -> bool:
    """判断给定位置是否在任一括号区间内"""
    for start, end in bracket_map:
        if start <= pos <= end:
            return True
    return False


def _is_valid_year_context(text: str, match_start: int, match_end: int) -> bool:
    """检查年份所在上下文是否合理，排除非年份数字

    Args:
        text: 完整句子
        match_start: 年份匹配的起始位置
        match_end: 年份匹配的结束位置

    Returns:
        True 表示是合理的年份上下文
    """
    # 检查前方字符：排除数学运算符和近似符号
    prefix = text[:match_start].rstrip()
    if prefix and _EXCLUDE_PREFIX_PATTERN.search(prefix):
        return False

    # 检查后方字符：排除度量单位
    suffix = text[match_end:]
    if _UNIT_PATTERN.match(suffix):
        return False

    # 排除小数点情况（如 "3.1920" 中的 1920）
    if match_start >= 2 and text[match_start - 1] == '.' and text[match_start - 2].isdigit():
        return False

    # 排除数字紧接前方（如 "样本量n=2020"）
    if match_start > 0 and text[match_start - 1].isdigit():
        return False

    # 排除连字符连接的编号序列（如 "ISBN 978-2020-1234"）
    if match_start >= 2 and text[match_start - 1] == '-' and text[match_start - 2].isdigit():
        return False
    if match_end < len(text) and text[match_end] == '-' and match_end + 1 < len(text) and text[match_end + 1].isdigit():
        return False

    # 排除数字紧跟后方（如 "20201234" 连续数字，但 "2020)" 或 "2020," 允许）
    if match_end < len(text) and text[match_end].isdigit():
        return False

    # 排除 "Month Day, Year" 格式的日期（如 "November 16, 2022"、"March 5, 2021"）
    _MONTH_NAMES = (
        'January', 'February', 'March', 'April', 'May', 'June',
        'July', 'August', 'September', 'October', 'November', 'December',
        'Jan', 'Feb', 'Mar', 'Apr', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    )
    prefix_stripped = text[:match_start].rstrip()
    # 检查前方是否为 "Month [Day,]" 模式
    month_day_pattern = re.compile(
        r'\b(?:' + '|'.join(_MONTH_NAMES) + r')\b\s*\d{1,2}\s*,\s*$',
        re.IGNORECASE
    )
    if month_day_pattern.search(prefix_stripped):
        return False

    return True


# ── 主函数 ──────────────────────────────────────────────────────

def extract_years(text: str) -> list[YearMention]:
    """从单个句子中提取所有年份提及

    Args:
        text: 待分析的句子文本

    Returns:
        提取到的年份列表，按位置排序
    """
    if not text or not text.strip():
        return []

    results: list[YearMention] = []
    # 已被年代词覆盖的位置区间，防止重复提取
    covered_ranges: list[tuple[int, int]] = []

    # 构建括号映射
    bracket_map = _build_bracket_map(text)

    # ── 模式1：中文文献编号说明 ──
    # "在文【7,8】中"、"在文[7,8]中"、"文献[7]"、"文献【7,8】"
    ref_num_pattern = re.compile(
        r'(?:在)?文(?:献)?[【\[]\s*[\d,，\s]+\s*[】\]](?:中)?'
    )
    for m in ref_num_pattern.finditer(text):
        results.append(YearMention(
            year="ref",
            position=m.start(),
            in_bracket=_is_in_bracket(m.start(), bracket_map),
            is_decade=False,
            is_ref_number=True,
        ))
        covered_ranges.append((m.start(), m.end()))
        logger.debug(f"提取到文献编号说明: '{m.group()}' 位置={m.start()}")

    # ── 模式2：年代词 ──
    # "the 1950s"、"the late 1970s"、"the early 1980s"、"the mid-1990s"
    # "in the early 1980s"
    decade_pattern = re.compile(
        r'(?:the\s+)?'                           # 可选的 the
        r'(?:(?:early|late|mid)[\s\-])?'          # 可选的 early/late/mid
        r'(\d{4})s',                              # 年代数字 + s
        re.IGNORECASE
    )
    for m in decade_pattern.finditer(text):
        year_num = int(m.group(1))
        if year_num < _YEAR_MIN or year_num > _YEAR_MAX:
            continue

        # 年代词的 year 字段存 "1950s" 格式
        year_str = f"{m.group(1)}s"

        results.append(YearMention(
            year=year_str,
            position=m.start(),
            in_bracket=_is_in_bracket(m.start(), bracket_map),
            is_decade=True,
            is_ref_number=False,
        ))
        covered_ranges.append((m.start(), m.end()))
        logger.debug(f"提取到年代词: '{m.group()}' -> {year_str} 位置={m.start()}")

    # ── 模式3：精确年份 ──
    # 匹配独立的四位数年份
    year_pattern = re.compile(r'(\d{4})')
    for m in year_pattern.finditer(text):
        year_num = int(m.group(1))

        # 范围校验
        if year_num < _YEAR_MIN or year_num > _YEAR_MAX:
            continue

        # 检查是否已被年代词或文献编号覆盖
        pos = m.start()
        is_covered = False
        for cstart, cend in covered_ranges:
            if cstart <= pos < cend:
                is_covered = True
                break
        if is_covered:
            continue

        # 上下文有效性校验
        if not _is_valid_year_context(text, m.start(), m.end()):
            continue

        results.append(YearMention(
            year=m.group(1),
            position=pos,
            in_bracket=_is_in_bracket(pos, bracket_map),
            is_decade=False,
            is_ref_number=False,
        ))
        logger.debug(f"提取到精确年份: {m.group(1)} 位置={pos}")

    # 按位置排序
    results.sort(key=lambda ym: ym.position)

    if results:
        logger.debug(f"共提取到 {len(results)} 个年份提及")
    return results
