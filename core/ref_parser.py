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
    doi: str = ""  # DOI 标识符


def extract_references_section(full_text: str) -> str:
    """从全文中提取参考文献部分

    策略：找到最后一个匹配的"参考文献"或"References"标题，
    因为前面可能有表格中的 "Reference" 列标题。
    """
    # 匹配中文"参考文献"或英文"References"
    patterns = [
        r'参考文献\s*(?:\(References\)\s*)?[：:]?\s*\n',
        r'References\s*[：:]?\s*\n',
        r'参\s*考\s*文\s*献',
    ]
    # 使用最后一个匹配（避免匹配到表格中的 "Reference" 列标题）
    last_pos = -1
    for pattern in patterns:
        for match in re.finditer(pattern, full_text, re.IGNORECASE):
            if match.start() > last_pos:
                last_pos = match.start()

    if last_pos == -1:
        return ""

    return full_text[last_pos:]


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

    # 提取 DOI
    doi_match = re.search(r'(?:DOI|doi)[：:\s]*\s*(10\.\d{4,}/\S+)', text)
    if not doi_match:
        doi_match = re.search(r'(10\.\d{4,}/\S+)', text)
    if doi_match:
        ref.doi = doi_match.group(1).rstrip('.')

    return ref


def _parse_english_ref(text: str) -> Reference:
    """解析英文参考文献

    支持多种格式：
    - 格式A（含[J]标记）: Author1 A, Author2 B. Title [J]. Journal, Year, Vol(Issue): Pages.
    - 格式B（不含[J]，冒号分隔）: Author1, A., Author2, B., et al.: Title. Journal Vol, Pages (Year)
    - 格式C（Springer风格）: Author1, A., Author2, B.: Title. J. Name Vol, Pages (Year)
    """
    ref = Reference(index=0, raw_text=text)
    ref.ref_type, ref.is_journal = _detect_ref_type(text)

    # 尝试格式A: 含 [J] 标记
    author_title_match = re.match(
        r'(.+?)[．.]\s*(.+?)\s*\[[JCDMPS]\]',
        text, re.IGNORECASE
    )
    if author_title_match:
        author_part = author_title_match.group(1).strip()
        ref.title = author_title_match.group(2).strip()

        author_part = re.sub(r',?\s*et\s*al\.?$', '', author_part, flags=re.IGNORECASE)
        raw_authors = re.split(r',\s*(?=[A-Z])', author_part)
        ref.authors = [a.strip() for a in raw_authors if a.strip()]
        if ref.authors:
            ref.first_author = ref.authors[0]

        # 提取期刊
        journal_match = re.search(r'\[[Jj]\][．.]\s*(.+?)[,，]\s*\d{4}', text)
        if journal_match:
            ref.journal = journal_match.group(1).strip()

        # 提取年份和卷期
        year_match = re.search(r'[,．.]\s*(\d{4})\s*[,]', text)
        if year_match:
            ref.year = year_match.group(1)
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
    else:
        # 尝试格式B/C: 冒号分隔作者和标题
        # Author1, A., Author2, B., et al.: Title. Journal Vol, Pages (Year)
        colon_match = re.match(
            r'(.+?):\s*(.+)',
            text, re.DOTALL,
        )
        if colon_match:
            author_part = colon_match.group(1).strip()
            rest = colon_match.group(2).strip()

            # 解析作者（格式：Surname, I., Surname2, I2., et al.）
            author_part_clean = re.sub(r',?\s*et\s*al\.?$', '', author_part, flags=re.IGNORECASE)
            # 按 ", " 后跟大写字母分割（但保留 "Surname, I." 中的逗号）
            # 策略：每个作者是 "Surname, I." 格式，用 ", " + 大写字母 分割
            raw_authors = re.split(r',\s+(?=[A-Z][a-z])', author_part_clean)
            ref.authors = [a.strip().rstrip(',') for a in raw_authors if a.strip()]
            if ref.authors:
                ref.first_author = ref.authors[0]

            # 从 rest 中提取标题、期刊、卷、页码、年份
            # 格式: Title. Journal Vol, Pages (Year)
            # 或: "Title". Journal Vol, Pages (Year)
            title_match = re.match(
                r'["\u201c]?(.+?)["\u201d]?\.\s*(.+)',
                rest, re.DOTALL,
            )
            if title_match:
                ref.title = title_match.group(1).strip()
                journal_part = title_match.group(2).strip()

                # 提取年份（通常在括号中末尾）
                year_match = re.search(r'\((\d{4})\)', journal_part)
                if year_match:
                    ref.year = year_match.group(1)

                # 提取期刊名、卷、页码
                # "Science 350, 938–943 (2015)"
                # "J. Mater. Chem. A 4, 6639–6644 (2016)"
                # "Adv. Energy Mater. 8, 1801156 (2018)"
                jvp_match = re.match(
                    r'(.+?)\s+(\d+)\s*(?:\((\d+)\))?\s*,\s*(.+?)(?:\s*\(\d{4}\))?$',
                    journal_part.strip(),
                )
                if jvp_match:
                    ref.journal = jvp_match.group(1).strip()
                    ref.volume = jvp_match.group(2)
                    if jvp_match.group(3):
                        ref.issue = jvp_match.group(3)
                    pages = jvp_match.group(4).strip().rstrip('.')
                    # 清理页码中可能的年份括号残留
                    pages = re.sub(r'\s*\(\d{4}\)\s*$', '', pages).strip()
                    ref.pages = pages.replace('\u2013', '-').replace('–', '-')
                else:
                    # 简单模式：只提取期刊名
                    simple_j = re.match(r'(.+?)\s*\d', journal_part)
                    if simple_j:
                        ref.journal = simple_j.group(1).strip().rstrip(',')

    # 提取 DOI
    doi_match = re.search(r'(?:DOI|doi)[：:\s]*\s*(10\.\d{4,}/\S+)', text)
    if not doi_match:
        doi_match = re.search(r'(10\.\d{4,}/\S+)', text)
    if doi_match:
        ref.doi = doi_match.group(1).rstrip('.')

    return ref


def _parse_apa_ref(text: str, index: int) -> Reference:
    """解析 APA 格式参考文献

    格式：Author, F., Author2, F., Year. Title. Journal Vol(Issue), Pages.
    例：Pitteti, K.H., Jackson, J.A., Stubbs, N.B., 1989. Fitness level... Adapt. Phys. Act. Q. 6 (4), 354–370.
    """
    ref = Reference(index=index, raw_text=text)
    ref.ref_type, ref.is_journal = _detect_ref_type(text)

    # 提取年份：支持两种格式
    # APA 传统：", 1989."
    # Cell Press：" (1989)."
    year_match = re.search(r'[,(]\s*(\d{4})\s*[).]\s*', text)
    if not year_match:
        return ref
    ref.year = year_match.group(1)
    year_pos = year_match.end()

    # 作者部分（年份之前）
    author_part = text[:year_match.start()].strip()
    # 按 ", " + 大写字母 分割（保留 "Surname, F." 格式）
    # APA 格式：每个作者是 "Surname, F.M." 或 "Surname, F."
    raw_authors = re.split(r',\s+(?=[A-Z][a-z])', author_part)
    ref.authors = [a.strip().rstrip(',') for a in raw_authors if a.strip()]
    if ref.authors:
        ref.first_author = ref.authors[0]

    # 标题和期刊（年份之后，去掉开头可能的 ". " 残留）
    rest = re.sub(r'^[\.\s]+', '', text[year_pos:]).strip()
    # 标题以句号结束
    title_match = re.match(r'(.+?)\.\s+(.+)', rest, re.DOTALL)
    if title_match:
        ref.title = title_match.group(1).strip()
        journal_part = title_match.group(2).strip()

        # 期刊格式：Journal Vol (Issue), Pages.
        # 例：Adapt. Phys. Act. Q. 6 (4), 354–370.
        jvp = re.match(
            r'(.+?)\s+(\d+)\s*(?:\((\d+)\))?\s*,\s*([\d\u2013\-–]+)',
            journal_part,
        )
        if jvp:
            ref.journal = jvp.group(1).strip().rstrip('.')
            ref.volume = jvp.group(2)
            if jvp.group(3):
                ref.issue = jvp.group(3)
            ref.pages = jvp.group(4).replace('\u2013', '-').replace('–', '-')
        else:
            # 只提取期刊名
            simple = re.match(r'(.+?)\s+\d', journal_part)
            if simple:
                ref.journal = simple.group(1).strip().rstrip('.')

    # 提取 DOI
    doi_match = re.search(r'(?:DOI|doi)[：:\s]*\s*(10\.\d{4,}/\S+)', text)
    if not doi_match:
        doi_match = re.search(r'(10\.\d{4,}/\S+)', text)
    if doi_match:
        ref.doi = doi_match.group(1).rstrip('.')

    return ref


def _is_apa_format(ref_section: str) -> bool:
    """检测参考文献是否为 APA/Cell Press 格式（无编号，作者+年份开头）"""
    sample = ref_section[:2000]
    # 传统 APA：Surname, F., ..., Year.
    apa_lines = re.findall(r'\n[A-Z][a-z]+,\s+[A-Z]\..*?,\s+\d{4}\.', sample)
    if len(apa_lines) >= 2:
        return True
    # Cell Press 格式：Surname, F. (Year).
    cell_lines = re.findall(r'\n[A-Z][a-z]+,\s+[A-Z].*?\(\d{4}\)\.', sample)
    return len(cell_lines) >= 2


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

    # 格式3: APA 格式（无编号）— 优先检测，避免误用编号格式
    if _is_apa_format(ref_section):
        return _parse_apa_references(ref_section)

    # 按编号分割：支持两种格式
    # 格式1: [数字] — 中文论文常见，也用于英文论文正文引用
    # 格式2: \t数字.\t — 英文论文常见（Springer等），仅在格式1无结果时使用
    entries = re.split(r'\n\s*\[(\d+)\]\s*', ref_section)

    # 格式1至少应该有 3 个条目（标题 + 编号1 + 内容1）
    if len(entries) < 3:
        # 尝试格式2: tab + 数字 + 点 + tab
        entries_dot = re.split(r'\n\s*\t?\s*(\d+)\.\t\s*', ref_section)
        if len(entries_dot) >= 3:
            entries = entries_dot

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


def _parse_apa_references(ref_section: str) -> list[Reference]:
    """将 APA/Cell Press 格式参考文献段落解析为 Reference 列表"""
    # 新条目特征：以 "Surname, F." 或 "Surname, F.M." 开头
    NEW_ENTRY = re.compile(r'^[A-Z][a-z]+,\s+[A-Z][\w]*\.')

    # 先把所有行合并，再按新条目边界重新分割
    raw_lines = [l.strip() for l in ref_section.split('\n') if l.strip()]

    # 把连续行归并到各自条目
    entries: list[str] = []
    current: list[str] = []
    for line in raw_lines:
        if NEW_ENTRY.match(line) and current:
            entries.append(' '.join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        entries.append(' '.join(current))

    references = []
    for i, entry_text in enumerate(entries, 1):
        entry_text = re.sub(r'\s+', ' ', entry_text).strip()
        if not re.search(r'[,(]\s*\d{4}[).]', entry_text):
            continue
        ref = _parse_apa_ref(entry_text, i)
        if ref.year:
            references.append(ref)

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
