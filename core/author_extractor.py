"""作者提取模块

职责：
- 从单个句子中提取所有作者姓名提及
- 支持英文（Smith et al.、Smith and Jones）和中文（张三等、张三和李四）
- 识别作者是否在括号内、是否有 et al. 修饰
- 关联引用编号
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────

@dataclass
class AuthorMention:
    """作者姓名提及"""
    name: str               # 作者名（如 "Smith"、"张三"）
    position: int           # 在句中的起始字符位置
    in_bracket: bool        # 是否在括号内（圆括号或方括号）
    with_et_al: bool        # 是否有 "et al." 修饰
    ref_numbers: list[int] = field(default_factory=list)  # 关联的引用编号
    preceded_by_prep: bool = False  # 是否位于 by/as 等介词之后（如 "described by Smith"）


# ── 排除词表 ──────────────────────────────────────────────────────

# 常见非作者名的大写开头英文词
_EXCLUDE_WORDS: set[str] = {
    # 月份
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
    "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    # 学术文档结构词
    "Table", "Tables", "Figure", "Figures", "Fig", "Figs",
    "Section", "Sections", "Chapter", "Chapters",
    "Equation", "Equations", "Eq", "Eqs",
    "Theorem", "Theorems", "Lemma", "Lemmas",
    "Appendix", "Abstract", "Introduction", "Method", "Methods",
    "Result", "Results", "Discussion", "Conclusion", "Conclusions",
    "Acknowledgment", "Acknowledgments", "Acknowledgement", "Acknowledgements",
    "Supplementary", "Supporting", "Electronic", "Online",
    # 常见学术/描述词
    "The", "This", "That", "These", "Those", "Here", "There", "However",
    "Although", "Because", "Since", "While", "When", "Where", "Which",
    "Furthermore", "Moreover", "Therefore", "Thus", "Hence", "Meanwhile",
    "Recently", "Generally", "Typically", "Notably", "Importantly",
    "Specifically", "Interestingly", "Surprisingly", "Unfortunately",
    "Similarly", "Conversely", "Additionally", "Alternatively",
    "For", "And", "But", "With", "From", "Into", "Over",
    "After", "Before", "During", "Between", "Among", "Through",
    # 化学/科学常见词
    "Fig", "Figs", "Ref", "Refs", "Vol", "No",
    "DNA", "RNA", "ATP", "NMR", "XRD", "SEM", "TEM", "AFM",
    "UV", "IR", "MS", "GC", "HPLC", "NaOH", "HCl",
    # 国家/地区
    "China", "Japan", "Korea", "India", "Germany", "France",
    "England", "America", "Europe", "Asia", "Africa",
    "United", "States", "Kingdom",
    # 其他
    "University", "Institute", "Laboratory", "Center", "Department",
    "Journal", "Science", "Nature", "Chemical", "Physical",
    "International", "National", "American", "European", "Chinese",
    "New", "All", "Each", "Some", "Many", "Most", "Several", "Various",
    "First", "Second", "Third", "Next", "Last",
    "In", "On", "At", "By", "To", "Of", "It", "As", "So", "If",
    "Based", "According", "Compared", "Due", "Given", "Using",
    "Note", "Data", "Both", "Such", "Other", "Total",
    "Aqueous", "Among",
}

# 小写排除集合（加速查找）
_EXCLUDE_WORDS_LOWER: set[str] = {w.lower() for w in _EXCLUDE_WORDS}

# 常见中文姓氏（前100个高频姓）
_CHINESE_SURNAMES: set[str] = {
    "王", "李", "张", "刘", "陈", "杨", "黄", "赵", "吴", "周",
    "徐", "孙", "马", "朱", "胡", "郭", "何", "林", "高", "罗",
    "郑", "梁", "谢", "宋", "唐", "许", "邓", "韩", "冯", "曹",
    "彭", "曾", "萧", "田", "董", "潘", "袁", "蔡", "蒋", "余",
    "于", "杜", "叶", "程", "魏", "苏", "吕", "丁", "任", "卢",
    "沈", "姚", "钟", "姜", "崔", "谭", "陆", "范", "汪", "廖",
    "石", "金", "贾", "韦", "夏", "付", "方", "邹", "熊", "白",
    "孟", "秦", "邱", "侯", "江", "尹", "薛", "闫", "段", "雷",
    "龙", "史", "陶", "贺", "毛", "郝", "顾", "龚", "邵", "万",
    "覃", "武", "钱", "戴", "严", "莫", "孔", "向", "常", "温",
    "欧阳", "上官", "司马", "诸葛", "东方", "皇甫", "令狐", "慕容",
}


# ── 括号范围构建 ──────────────────────────────────────────────────

def _build_bracket_map(text: str) -> list[tuple[int, int]]:
    """构建括号起止位置映射

    支持：()、（）

    Returns:
        括号区间列表 [(start, end), ...]
    """
    brackets: list[tuple[int, int]] = []
    stack: list[int] = []
    open_chars = {'(', '（'}
    close_chars = {')', '）'}

    for i, ch in enumerate(text):
        if ch in open_chars:
            stack.append(i)
        elif ch in close_chars and stack:
            start = stack.pop()
            brackets.append((start, i))

    return brackets


def _is_in_bracket(pos: int, bracket_map: list[tuple[int, int]]) -> bool:
    """判断给定位置是否在任一括号区间内"""
    for start, end in bracket_map:
        if start < pos < end:
            return True
    return False


# ── 引用编号提取 ──────────────────────────────────────────────────

def _extract_nearby_ref_numbers(text: str, position: int, window: int = 30) -> list[int]:
    """提取作者名附近的引用编号

    查找 [数字] 或 [数字,数字] 格式的引用编号。

    Args:
        text: 完整句子
        position: 作者名位置
        window: 搜索窗口大小

    Returns:
        引用编号列表
    """
    # 在作者名后方搜索引用编号
    search_start = position
    search_end = min(len(text), position + window)
    search_text = text[search_start:search_end]

    ref_numbers = []
    for m in re.finditer(r'\[(\d+(?:\s*[,，]\s*\d+)*)\]', search_text):
        nums = re.findall(r'\d+', m.group(1))
        ref_numbers.extend(int(n) for n in nums)

    return ref_numbers


# ── 英文作者提取 ──────────────────────────────────────────────────

def _extract_english_authors(text: str, bracket_map: list[tuple[int, int]]) -> list[AuthorMention]:
    """提取英文作者名

    匹配模式（按优先级）：
    1. 括号内行内引用：(Smith, 2020) / (Smith et al., 2020; Jones, 2019)
    2a. 团队引用：Smith and co-workers / Smith and colleagues
    2b. by 引用：by Smith / by Smith et al.
    2c. 叙述引用：Smith et al. (2020) / Smith and Jones (2019)
    3. 纯文本作者名 + 附近引用编号
    """
    results: list[AuthorMention] = []
    used_positions: set[int] = set()  # 避免重复提取

    # ── 模式1：括号内行内引用 ──
    # (Smith, 2020) / (Smith et al., 2020) / (Smith and Jones, 2020)
    # (Smith et al., 2020; Jones et al., 2019)
    bracket_cite_pattern = re.compile(
        r'\(([^)]+)\)'
    )
    for m in bracket_cite_pattern.finditer(text):
        bracket_content = m.group(1)
        bracket_start = m.start()

        # 分号分隔多个引用
        citations = re.split(r'\s*;\s*', bracket_content)
        for cite in citations:
            # 匹配: AuthorName [and AuthorName2] [et al.][,] Year
            cite_match = re.match(
                r'([A-Z][a-zA-Z\-\']+)'        # 第一作者姓氏
                r'(?:\s+and\s+[A-Z][a-zA-Z\-\']+)?'  # 可选的第二作者
                r'(?:\s+et\s+al\.?)?'           # 可选的 et al.
                r'\s*,?\s*'
                r'(\d{4}[a-z]?)',               # 年份（可能带字母后缀如 2020a）
                cite.strip()
            )
            if cite_match:
                author_name = cite_match.group(1)
                if author_name.lower() in _EXCLUDE_WORDS_LOWER:
                    continue

                # 计算在原文中的位置
                name_pos_in_bracket = cite.find(author_name)
                abs_pos = bracket_start + 1 + (bracket_content.find(cite.strip())) + name_pos_in_bracket

                if abs_pos in used_positions:
                    continue
                used_positions.add(abs_pos)

                has_et_al = bool(re.search(r'et\s+al', cite))
                ref_nums = _extract_nearby_ref_numbers(text, m.end())

                results.append(AuthorMention(
                    name=author_name,
                    position=abs_pos,
                    in_bracket=True,
                    with_et_al=has_et_al,
                    ref_numbers=ref_nums,
                ))

    # ── 模式2a：Author and co-workers/coworkers/colleagues/collaborators ──
    # 匹配 "by Wang and co-workers" / "Wang and colleagues" 等
    coworker_pattern = re.compile(
        r'\b([A-Z][a-zA-Z\-\']{1,30})'                    # 作者姓氏
        r'\s+and\s+'
        r'(?:co-?\s*workers|colleagues|collaborators|'     # 团队词汇
        r'coauthors|co-?\s*authors|associates)',
        re.IGNORECASE,
    )
    for m in coworker_pattern.finditer(text):
        author_name = m.group(1)

        if author_name.lower() in _EXCLUDE_WORDS_LOWER:
            continue
        if len(author_name) < 2 or len(author_name) > 25:
            continue

        pos = m.start()
        if pos in used_positions:
            continue
        used_positions.add(pos)

        in_bracket = _is_in_bracket(pos, bracket_map)
        ref_nums = _extract_nearby_ref_numbers(text, m.end(), window=80)

        results.append(AuthorMention(
            name=author_name,
            position=pos,
            in_bracket=in_bracket,
            with_et_al=True,  # co-workers 等同于 et al.
            ref_numbers=ref_nums,
        ))

    # ── 模式2b：by Author 引用模式 ──
    # 匹配 "by Smith" / "by Smith et al." 后跟引用编号
    by_author_pattern = re.compile(
        r'\bby\s+([A-Z][a-zA-Z\-\']{1,30})'              # by + 作者姓氏
        r'(?:\s+and\s+[A-Z][a-zA-Z\-\']+)?'              # 可选的第二作者
        r'(\s+et\s+al\.?)?'                               # 可选的 et al.
    )
    for m in by_author_pattern.finditer(text):
        author_name = m.group(1)

        if author_name.lower() in _EXCLUDE_WORDS_LOWER:
            continue
        if len(author_name) < 2 or len(author_name) > 25:
            continue

        pos = m.start() + len('by') + (m.start(1) - m.start())  # 指向作者名
        # 修正：直接用 group(1) 的位置
        pos = m.start(1)

        if pos in used_positions:
            continue
        used_positions.add(pos)

        has_et_al = m.group(2) is not None
        in_bracket = _is_in_bracket(pos, bracket_map)
        ref_nums = _extract_nearby_ref_numbers(text, m.end(), window=80)

        results.append(AuthorMention(
            name=author_name,
            position=pos,
            in_bracket=in_bracket,
            with_et_al=has_et_al,
            ref_numbers=ref_nums,
            preceded_by_prep=True,  # by_author_pattern 明确是介词宾语
        ))

    # ── 模式2d：Surname, Year verb 格式（如 "Cornet, 1989 described"）──
    # 作者姓氏后跟逗号+年份，年份不在括号内
    surname_year_pattern = re.compile(
        r'\b([A-Z][a-zA-Z\-\']{1,30})'   # 作者姓氏
        r',\s*'
        r'(\d{4}[a-z]?)'                  # 年份（逗号后，不在括号内）
        r'\b'
    )
    for m in surname_year_pattern.finditer(text):
        author_name = m.group(1)
        if author_name.lower() in _EXCLUDE_WORDS_LOWER:
            continue
        if len(author_name) < 2 or len(author_name) > 25:
            continue
        pos = m.start()
        if pos in used_positions:
            continue
        # 确保不在括号内
        if _is_in_bracket(pos, bracket_map):
            continue
        used_positions.add(pos)
        ref_nums = _extract_nearby_ref_numbers(text, m.end(), window=50)
        results.append(AuthorMention(
            name=author_name,
            position=pos,
            in_bracket=False,
            with_et_al=False,
            ref_numbers=ref_nums,
        ))

    # ── 模式2e：Author [and Author2] + 裸年份（不在括号内）──
    # 处理 "Bornmann and Marx first introduced ... in 2014" 类结构
    # 作者后方 100 字符内有裸年份（非括号内）
    narrative_bare_year_pattern = re.compile(
        r'\b([A-Z][a-zA-Z\-\']{1,30})'         # 第一作者姓氏
        r'(?:\s+and\s+[A-Z][a-zA-Z\-\']+)?'    # 可选的第二作者
        r'(?!\s*,?\s*\d{4})'                    # 排除紧跟逗号+年份（已由模式2d处理）
    )
    for m in narrative_bare_year_pattern.finditer(text):
        author_name = m.group(1)
        if author_name.lower() in _EXCLUDE_WORDS_LOWER:
            continue
        if len(author_name) < 2 or len(author_name) > 25:
            continue
        pos = m.start()
        if pos in used_positions:
            continue
        # 在作者名后方 120 字符内寻找裸年份（不在括号内）
        search_end = min(len(text), m.end() + 120)
        bare_year_m = re.search(r'(?<!\()\b(1[89]\d{2}|20[012]\d)\b(?!\))', text[m.end():search_end])
        if not bare_year_m:
            continue
        # 确保年份不在括号内
        year_abs_pos = m.end() + bare_year_m.start()
        if _is_in_bracket(year_abs_pos, bracket_map):
            continue
        used_positions.add(pos)
        ref_nums = _extract_nearby_ref_numbers(text, m.end(), window=50)
        results.append(AuthorMention(
            name=author_name,
            position=pos,
            in_bracket=_is_in_bracket(pos, bracket_map),
            with_et_al=False,
            ref_numbers=ref_nums,
        ))

    # ── 模式2c：叙述引用 Author et al. (Year) 或 Author and Author2 (Year) ──
    narrative_pattern = re.compile(
        r'\b([A-Z][a-zA-Z\-\']{1,30})'         # 作者姓氏
        r'(?:\s+and\s+[A-Z][a-zA-Z\-\']+)?'    # 可选的第二作者
        r'(\s+et\s+al\.?)?'                      # 可选的 et al.
        r'\s*'
        r'(?:\((\d{4}[a-z]?)\))?'               # 可选的括号内年份
    )
    for m in narrative_pattern.finditer(text):
        author_name = m.group(1)

        # 排除常见非作者词
        if author_name.lower() in _EXCLUDE_WORDS_LOWER:
            continue

        # 排除过短或过长的名字
        if len(author_name) < 2 or len(author_name) > 25:
            continue

        pos = m.start()

        # 检查是否已提取过
        if pos in used_positions:
            continue

        # 必须有 et al. 或括号内年份才认为是作者引用
        has_et_al = m.group(2) is not None
        has_year = m.group(3) is not None

        if not has_et_al and not has_year:
            # 纯姓氏，没有 et al. 也没有年份 → 检查后面是否有引用编号
            ref_nums = _extract_nearby_ref_numbers(text, m.end(), window=50)
            if not ref_nums:
                continue

        in_bracket = _is_in_bracket(pos, bracket_map)

        # 避免重复
        used_positions.add(pos)

        ref_nums = _extract_nearby_ref_numbers(text, m.end(), window=50)

        results.append(AuthorMention(
            name=author_name,
            position=pos,
            in_bracket=in_bracket,
            with_et_al=has_et_al,
            ref_numbers=ref_nums,
        ))

    return results


# ── 中文作者提取 ──────────────────────────────────────────────────

def _extract_chinese_authors(text: str, bracket_map: list[tuple[int, int]]) -> list[AuthorMention]:
    """提取中文作者名

    不依赖姓氏表，改为通过锚定信号反向定位作者名：
    1. 叙述引用：2-4个汉字 + 等 + [N]/（年份）  如 "丁传波等[3]"、"索丰平等（2007）"
    2. 无"等"直接跟引用编号：2-4个汉字 + [N]
    3. 括号内引用：（张三，2020）
    """
    results: list[AuthorMention] = []
    used_positions: set[int] = set()

    # 排除字符集：这些字结尾的不是姓名（含义词/功能词）
    _NOT_NAME_TAIL = set('的了在是不有这和与或而且也都把被将其那就着要能说会没还如但上下大小多少面向时')

    # 模式1/2：叙述引用（带或不带"等"），后跟引用编号或括号年份
    narrative_re = re.compile(
        r'([\u4e00-\u9fff]{2,4})'          # group1: 2-4个汉字（姓名）
        r'(等?)'                            # group2: 有无"等"
        r'\s*'
        r'([\[【]\d+(?:[,，\s]\d+)*[\]】]'  # group3: [N] 锚定
        r'|[（(]\s*\d{4})'                  # 或 （年份） 锚定
    )
    for m in narrative_re.finditer(text):
        name = m.group(1)
        has_et_al = bool(m.group(2))
        pos = m.start()

        if pos in used_positions:
            continue
        # 排除末字是功能字的非姓名词组
        if name[-1] in _NOT_NAME_TAIL:
            continue

        used_positions.add(pos)
        in_bracket = _is_in_bracket(pos, bracket_map)
        ref_nums = _extract_nearby_ref_numbers(text, m.start(3), window=50)
        results.append(AuthorMention(
            name=name,
            position=pos,
            in_bracket=in_bracket,
            with_et_al=has_et_al,
            ref_numbers=ref_nums,
        ))

    # 模式3：括号内引用 （张三，2020）/（张三等，2020）
    bracket_cn_re = re.compile(
        r'[（(]'
        r'([\u4e00-\u9fff]{2,4})'   # group1: 姓名
        r'(等?)'                    # group2: 有无等
        r'\s*[，,]\s*'
        r'\d{4}'                    # 年份
        r'\s*[）)]'
    )
    for m in bracket_cn_re.finditer(text):
        name = m.group(1)
        has_et_al = bool(m.group(2))
        pos = m.start(1)

        if pos in used_positions:
            continue
        if name[-1] in _NOT_NAME_TAIL:
            continue

        used_positions.add(pos)
        results.append(AuthorMention(
            name=name,
            position=pos,
            in_bracket=True,
            with_et_al=has_et_al,
            ref_numbers=[],
        ))

    return results


# ── 主函数 ──────────────────────────────────────────────────────

def extract_authors(text: str) -> list[AuthorMention]:
    """从句子中提取所有作者姓名提及

    支持英文和中文作者名，识别括号内外位置。

    Args:
        text: 待分析的句子文本

    Returns:
        作者提及列表，按位置排序
    """
    if not text or not text.strip():
        return []

    bracket_map = _build_bracket_map(text)

    # 英文作者
    en_authors = _extract_english_authors(text, bracket_map)

    # 中文作者
    cn_authors = _extract_chinese_authors(text, bracket_map)

    # 合并并按位置排序
    all_authors = en_authors + cn_authors
    all_authors.sort(key=lambda a: a.position)

    # 去重：同一位置只保留一个
    deduped: list[AuthorMention] = []
    seen_positions: set[int] = set()
    for author in all_authors:
        if author.position not in seen_positions:
            deduped.append(author)
            seen_positions.add(author.position)

    if deduped:
        logger.debug(
            f"提取到 {len(deduped)} 个作者提及: "
            f"{[(a.name, a.position, a.in_bracket) for a in deduped]}"
        )

    return deduped
