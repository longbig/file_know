"""参考文献列表解析模块

职责：
- 从论文全文中定位参考文献部分
- 解析每条参考文献的编号、作者、标题、期刊、年份、卷期页码、类型
"""

import re
from dataclasses import dataclass, field


@dataclass
class Reference:
    """单条参考文献"""
    index: int  # 编号 [1], [2], ...
    raw_text: str  # 原文
    authors: list[str] = field(default_factory=list)
    first_author: str = ""
    title: str = ""
    journal: str = ""
    year: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    ref_type: str = ""  # J=期刊, C=会议, D=学位论文, M=专著, P=专利, S=标准
    is_journal: bool = False


def extract_references_section(full_text: str) -> str:
    """从全文中提取参考文献部分"""
    # 匹配中文"参考文献"或英文"References"
    patterns = [
        r'参考文献\s*(?:\(References\)\s*)?[：:]?\s*\n',
        r'References?\s*[：:]?\s*\n',
        r'参\s*考\s*文\s*献',
    ]
    ref_start = -1
    for pattern in patterns:
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            ref_start = match.start()
            break

    if ref_start == -1:
        return ""

    return full_text[ref_start:]


def _detect_ref_type(text: str) -> tuple[str, bool]:
    """检测参考文献类型"""
    # 中文标注格式 [J], [C], [D], [M], [P], [S]
    type_match = re.search(r'\[([JCDMPS])\]', text, re.IGNORECASE)
    if type_match:
        t = type_match.group(1).upper()
        return t, t == "J"

    # 英文格式推断
    if re.search(r'\bConference\b|\bProceedings\b|\bWorkshop\b|C\]//', text, re.IGNORECASE):
        return "C", False
    if re.search(r'\bDissertation\b|\bThesis\b', text, re.IGNORECASE):
        return "D", False
    if re.search(r'\bPatent\b', text, re.IGNORECASE):
        return "P", False

    # 默认假设期刊
    return "J", True


def _parse_chinese_ref(text: str) -> Reference:
    """解析中文参考文献"""
    ref = Reference(index=0, raw_text=text)
    ref.ref_type, ref.is_journal = _detect_ref_type(text)

    # 提取作者部分（到句号之前）
    # 格式: 作者1，作者2，作者3．标题[J]．期刊，年，卷（期）：页码．
    author_title_match = re.match(
        r'(.+?)[．.]\s*(.+?)\s*\[[JCDMPS]\]',
        text, re.IGNORECASE
    )
    if author_title_match:
        author_part = author_title_match.group(1).strip()
        ref.title = author_title_match.group(2).strip()

        # 分割作者
        # 处理 "等" 和 "et al"
        author_part = re.sub(r'，等$|,\s*et\s*al\.?$', '', author_part, flags=re.IGNORECASE)
        authors = re.split(r'[，,]', author_part)
        ref.authors = [a.strip() for a in authors if a.strip()]
        if ref.authors:
            ref.first_author = ref.authors[0]

    # 提取年份
    year_match = re.search(r'[，,．.]\s*(\d{4})\s*[，,]', text)
    if year_match:
        ref.year = year_match.group(1)

    # 提取期刊名（在 [J]. 之后，年份之前）
    journal_match = re.search(r'\[[Jj]\][．.]\s*(.+?)[，,]\s*\d{4}', text)
    if journal_match:
        ref.journal = journal_match.group(1).strip()

    # 提取卷期页码
    vol_issue_match = re.search(r'(\d{4})\s*[，,]\s*(\d+)\s*[（(](\d+)[）)]\s*[：:]\s*(.+?)(?:[．.\n]|$)', text)
    if vol_issue_match:
        ref.year = vol_issue_match.group(1)
        ref.volume = vol_issue_match.group(2)
        ref.issue = vol_issue_match.group(3)
        ref.pages = vol_issue_match.group(4).strip().rstrip('.')

    return ref


def _parse_english_ref(text: str) -> Reference:
    """解析英文参考文献"""
    ref = Reference(index=0, raw_text=text)
    ref.ref_type, ref.is_journal = _detect_ref_type(text)

    # 英文格式: AUTHOR1 A B, AUTHOR2 C D. Title [J]. Journal, Year, Vol(Issue): Pages.
    # 或: Author1 A, Author2 B, et al. Title [J]. Journal, Year, Vol(Issue): Pages.
    author_title_match = re.match(
        r'(.+?)[．.]\s*(.+?)\s*\[[JCDMPS]\]',
        text, re.IGNORECASE
    )
    if author_title_match:
        author_part = author_title_match.group(1).strip()
        ref.title = author_title_match.group(2).strip()

        author_part = re.sub(r',?\s*et\s*al\.?$', '', author_part, flags=re.IGNORECASE)
        # 按逗号分隔但考虑 "LAST FIRST" 格式
        raw_authors = re.split(r',\s*(?=[A-Z])', author_part)
        ref.authors = [a.strip() for a in raw_authors if a.strip()]
        if ref.authors:
            ref.first_author = ref.authors[0]

    # 提取年份
    year_match = re.search(r'[,．.]\s*(\d{4})\s*[,]', text)
    if year_match:
        ref.year = year_match.group(1)

    # 提取期刊
    journal_match = re.search(r'\[[Jj]\][．.]\s*(.+?)[,，]\s*\d{4}', text)
    if journal_match:
        ref.journal = journal_match.group(1).strip()

    # 提取卷期页码
    vol_match = re.search(r'(\d{4})\s*,\s*(\d+)\s*\((\d+)\)\s*:\s*(.+?)(?:[．.\n]|$)', text)
    if not vol_match:
        vol_match = re.search(r'(\d{4})\s*,\s*(\d+)\s*[：:]\s*(.+?)(?:[．.\n]|$)', text)
    if vol_match:
        ref.year = vol_match.group(1)
        ref.volume = vol_match.group(2)
        if len(vol_match.groups()) == 4:
            ref.issue = vol_match.group(3)
            ref.pages = vol_match.group(4).strip().rstrip('.')
        else:
            ref.pages = vol_match.group(3).strip().rstrip('.')

    return ref


def parse_references(full_text: str) -> list[Reference]:
    """解析论文中的参考文献列表

    Args:
        full_text: 论文全文文本

    Returns:
        参考文献列表
    """
    ref_section = extract_references_section(full_text)
    if not ref_section:
        return []

    # 按编号 [1], [2] 等分割
    # 匹配 [数字] 开头的条目
    entries = re.split(r'\n\s*\[(\d+)\]\s*', ref_section)

    references = []
    # entries[0] 是 "参考文献" 标题前面的内容，跳过
    i = 1
    while i < len(entries) - 1:
        idx = int(entries[i])
        text = entries[i + 1].strip()
        # 清理换行
        text = re.sub(r'\s*\n\s*', ' ', text)

        # 判断中英文
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', text))

        if has_chinese:
            ref = _parse_chinese_ref(text)
        else:
            ref = _parse_english_ref(text)

        ref.index = idx
        ref.raw_text = text
        references.append(ref)
        i += 2

    return references


def find_reference_by_author_year(
    references: list[Reference],
    author: str,
    year: str,
) -> Reference | None:
    """根据作者和年份查找匹配的参考文献

    Args:
        references: 参考文献列表
        author: 作者名（中文全名或英文姓氏）
        year: 年份字符串

    Returns:
        匹配的 Reference 或 None
    """
    for ref in references:
        if ref.year != year:
            continue
        # 检查作者是否匹配
        if author in ref.first_author or ref.first_author in author:
            return ref
        # 也检查作者列表
        for a in ref.authors:
            if author in a or a in author:
                return ref
    return None
