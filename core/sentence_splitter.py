"""学术论文句子分割模块

职责：
- 将学术论文全文分割为独立句子
- 正确处理学术缩写词（et al. / e.g. / Fig. 等）不误分割
- 处理中英文混合文本的句子边界
- 排除参考文献段落
- 合并跨行文本
"""

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── 缩写词列表 ──────────────────────────────────────────────────────

# 学术缩写
_ACADEMIC_ABBREVS = [
    "et al", "e.g", "i.e", "vs", "etc", "Fig", "Figs",
    "Eq", "Eqs", "Ref", "Refs", "Vol", "No",
    "Dr", "Prof", "Mr", "Mrs", "Jr", "Sr",
    "Inc", "Ltd", "Corp",
    "approx", "ca", "cf", "viz",
]

# 月份缩写
_MONTH_ABBREVS = [
    "Jan", "Feb", "Mar", "Apr", "Jun", "Jul",
    "Aug", "Sep", "Oct", "Nov", "Dec",
]

# 合并所有缩写词，构建用于正则匹配的集合（小写）
_ALL_ABBREVS: set[str] = set()
for _abbr in _ACADEMIC_ABBREVS + _MONTH_ABBREVS:
    # 存储不带末尾句号的形式，统一小写用于比较
    _ALL_ABBREVS.add(_abbr.lower().rstrip("."))


# ── 数据结构 ─────────────────────────────────────────────────────────

@dataclass
class Sentence:
    """分割后的单个句子"""
    text: str           # 句子原文
    index: int          # 句子序号（从0开始）
    start_pos: int      # 在全文中的字符起始位置
    end_pos: int        # 字符结束位置
    prev_sentence: str  # 前一句（上下文），第一句为空字符串
    next_sentence: str  # 后一句（上下文），最后一句为空字符串


# ── 内部工具函数 ─────────────────────────────────────────────────────

def _remove_references_section(text: str) -> str:
    """定位并移除参考文献段落

    查找"参考文献"或"References"标题，截断其后的所有内容。
    """
    patterns = [
        # 中文"参考文献"，可能跟 (References)
        r'\n\s*参\s*考\s*文\s*献\s*(?:\(References\))?\s*[：:]?\s*\n',
        # 英文 References / Reference
        r'\n\s*References?\s*[：:]?\s*\n',
    ]
    earliest_pos = len(text)
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match and match.start() < earliest_pos:
            earliest_pos = match.start()

    if earliest_pos < len(text):
        logger.debug(f"参考文献段落定位于字符位置 {earliest_pos}，已截断")
        return text[:earliest_pos]
    return text


def _merge_broken_lines(text: str) -> str:
    """合并跨行文本

    规则：
    - 双换行（段落分隔）保留为换行
    - 单换行合并为空格（学术论文 PDF 常见的跨行断行）
    - 连续多个空行合并为一个换行
    """
    # 先统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 将连续两个及以上换行标记为段落分隔符（用特殊占位符）
    text = re.sub(r'\n\s*\n', '\n\n', text)

    # 用占位符保护段落分隔
    _PARA_SEP = "\x00PARASEP\x00"
    text = text.replace("\n\n", _PARA_SEP)

    # 单换行替换为空格
    text = text.replace("\n", " ")

    # 恢复段落分隔为换行
    text = text.replace(_PARA_SEP, "\n")

    # 清理多余空格
    text = re.sub(r' {2,}', ' ', text)

    return text.strip()


def _clean_header_footer_noise(text: str) -> str:
    """尽量去除页眉页脚噪音

    常见噪音模式：
    - 独立的页码行（纯数字行）
    - 期刊名 + 卷期号行
    - DOI 行
    """
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过纯数字行（页码）
        if re.match(r'^\d{1,4}$', stripped):
            continue
        # 跳过类似 "· 1234 ·" 的页码格式
        if re.match(r'^[·\-—\s]*\d{1,4}[·\-—\s]*$', stripped):
            continue
        # 跳过非常短的行且包含卷期信息（如 "Vol. 35 No. 2"）
        if len(stripped) < 60 and re.match(
            r'^.*(?:Vol\.|卷|No\.|期|第\s*\d+\s*[卷期]).*\d{4}.*$', stripped
        ):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _is_abbreviation(text_before_dot: str) -> bool:
    """判断句号前的文本是否以缩写词结尾

    Args:
        text_before_dot: 句号之前的文本

    Returns:
        True 表示这是缩写词的句号，不应分割
    """
    if not text_before_dot:
        return False

    # 检查是否是姓名缩写：单个大写字母后跟句号（如 "A." "B."）
    # 匹配末尾的 "X" （单个大写字母）
    if re.search(r'\b[A-Z]$', text_before_dot):
        return True

    # 提取末尾单词（缩写词可能包含句号，如 "e.g"）
    # 从末尾向前找到最后一个"词"
    match = re.search(r'([A-Za-z][A-Za-z.]*[A-Za-z])$|([A-Za-z])$', text_before_dot)
    if match:
        word = (match.group(1) or match.group(2)).rstrip(".")
        if word.lower() in _ALL_ABBREVS:
            return True

    return False


def _insert_section_boundaries(text: str) -> str:
    """在编号小节标题周围插入句子边界

    学术论文中编号标题（如 "3.1.1 Superconcentrated Electrolytes"）
    在 PDF 提取后可能与前后文本连接，没有标点分隔。

    策略：找到编号标题模式，在编号前面（如果前面没有句号）插入句号。
    标题本身会作为一个短句子被分割出来（无害），后面的正文自然成为新句子。
    """
    # 匹配 "数字.数字[.数字]" 编号模式（至少两级：3.1、3.1.1、3.1.1.1）
    # 编号前可能有空白/Unicode空格
    section_num_pattern = re.compile(
        r'([\s\u2002\u2003]+)'                  # group(1): 前面的空白
        r'(\d+(?:\.\d+)+\.?)'                    # group(2): 编号
        r'([\s\u2002\u2003]+[\x00-\x1f]*)'      # group(3): 编号后的空白和控制字符
        r'(?=[A-Z\u4e00-\u9fff"\u201c\u201d])'  # 前瞻：后跟大写/中文/引号
    )

    result = text
    offset = 0
    for m in section_num_pattern.finditer(text):
        # 检查编号前面是否已经有句号
        pre_pos = m.start()
        # 往前跳过空白，找到前一个非空白字符
        check_pos = pre_pos - 1
        while check_pos >= 0 and text[check_pos] in ' \t\u2002\u2003':
            check_pos -= 1
        if check_pos >= 0 and text[check_pos] in '.。!?！？':
            # 前面已经有句号，只需要确保句号后面能正确分割
            continue

        # 在编号前面插入句号
        insert_pos = m.start() + offset
        result = result[:insert_pos] + '.' + result[insert_pos:]
        offset += 1

    return result


def _split_by_sentence_boundaries(text: str) -> list[tuple[str, int, int]]:
    """在句子边界处分割文本

    返回 (句子文本, 起始位置, 结束位置) 的列表。

    分割点：
    - 英文句号 `.` + 后跟空格/换行/EOF（排除缩写词）
    - 中文句号 `。`
    - 感叹号 `！` `!`
    - 问号 `？` `?`

    保留引用标记 [1], [2,3], [4-6] 在句子中。
    """
    if not text.strip():
        return []

    # 结果列表：(句子文本, 起始位置, 结束位置)
    sentences: list[tuple[str, int, int]] = []
    current_start = 0

    # 用索引遍历逐字符判断
    i = 0
    length = len(text)

    while i < length:
        char = text[i]

        # ── 中文标点句子边界 ──
        if char in '。！？':
            # 中文标点后直接分割
            sent_text = text[current_start:i + 1].strip()
            if sent_text:
                sentences.append((sent_text, current_start, i + 1))
            current_start = i + 1
            i += 1
            continue

        # ── 英文标点句子边界 ──
        if char in '.!?':
            # 感叹号和问号：直接作为句子边界
            if char in '!?':
                # 确保后面跟空格、换行或 EOF
                if i + 1 >= length or text[i + 1] in ' \n\t':
                    sent_text = text[current_start:i + 1].strip()
                    if sent_text:
                        sentences.append((sent_text, current_start, i + 1))
                    current_start = i + 1
                    i += 1
                    continue

            # 英文句号处理
            if char == '.':
                # 检查是否为句子结束的句号：
                # 条件1：后面跟空格+大写字母、换行、或 EOF
                # 条件2：不是缩写词的一部分

                # 先检查后面的字符
                after_dot = ""
                j = i + 1
                # 跳过句号后紧跟的引用标记，如 [1], [2,3]
                while j < length and text[j] in ' ':
                    j += 1

                if j < length:
                    after_dot = text[j]

                is_end_of_sentence = False

                if i + 1 >= length:
                    # 文本末尾
                    is_end_of_sentence = True
                elif text[i + 1] == '\n':
                    # 句号后换行
                    is_end_of_sentence = True
                elif text[i + 1] == ' ':
                    # 句号后空格
                    # 检查空格后是否跟大写字母或引用标记 [
                    k = i + 2
                    while k < length and text[k] == ' ':
                        k += 1
                    if k < length:
                        next_char = text[k]
                        if next_char.isupper() or next_char in '[（""\u201c':
                            is_end_of_sentence = True
                        elif next_char.isdigit():
                            # 句号后空格 + 数字：检查是否是编号标题（如 "3.1.1 Title"）
                            # 向前扫描看是否是 "数字.数字" 模式的小节编号
                            rest = text[k:k+30]
                            if re.match(r'\d+(?:\.\d+)+', rest):
                                is_end_of_sentence = True
                        elif re.match(r'[\u4e00-\u9fff]', next_char):
                            # 句号后跟中文字符
                            is_end_of_sentence = True
                        # 小写字母开头 → 可能是句子继续，不分割
                    else:
                        # 空格后到文本末尾
                        is_end_of_sentence = True

                if is_end_of_sentence:
                    # 再检查是否是缩写词
                    text_before = text[current_start:i]
                    if not _is_abbreviation(text_before):
                        sent_text = text[current_start:i + 1].strip()
                        if sent_text:
                            sentences.append((sent_text, current_start, i + 1))
                        current_start = i + 1
                        i += 1
                        continue

        i += 1

    # 处理最后一段没有句号结尾的文本
    remaining = text[current_start:].strip()
    if remaining:
        sentences.append((remaining, current_start, length))

    return sentences


def _calculate_positions(
    sentences_raw: list[tuple[str, int, int]],
    preprocessed_text: str,
) -> list[tuple[str, int, int]]:
    """重新计算句子在预处理后文本中的精确位置

    分割函数返回的位置可能因为空格跳过而不精确，
    这里重新在预处理后的文本中定位每个句子。
    """
    result: list[tuple[str, int, int]] = []
    search_from = 0
    for sent_text, _, _ in sentences_raw:
        # 在文本中查找句子的精确位置
        pos = preprocessed_text.find(sent_text, search_from)
        if pos == -1:
            # 尝试用去除首尾空格的方式查找
            stripped = sent_text.strip()
            pos = preprocessed_text.find(stripped, search_from)
            if pos == -1:
                # 找不到就用上一次的结束位置
                pos = search_from
            sent_text = stripped

        end_pos = pos + len(sent_text)
        result.append((sent_text, pos, end_pos))
        search_from = end_pos

    return result


# ── 主函数 ───────────────────────────────────────────────────────────

def split_sentences(full_text: str) -> list[Sentence]:
    """将学术论文全文分割为句子列表

    处理流程：
    1. 去除页眉页脚噪音
    2. 移除参考文献段落
    3. 合并跨行文本
    4. 基于正则在句子边界处分割（排除缩写词）
    5. 后处理：去除空白句、构建上下文引用

    Args:
        full_text: 论文全文文本（通常由 pdf_parser 提取）

    Returns:
        Sentence 对象列表，按出现顺序排列
    """
    if not full_text or not full_text.strip():
        logger.warning("输入文本为空，返回空列表")
        return []

    # 规范化 PDF 连字符（ﬁ/ﬂ/ﬀ 等），避免标志词匹配失败
    full_text = (full_text
        .replace('\ufb00', 'ff').replace('\ufb01', 'fi').replace('\ufb02', 'fl')
        .replace('\ufb03', 'ffi').replace('\ufb04', 'ffl')
        .replace('\ufb05', 'st').replace('\ufb06', 'st')
    )

    logger.info(f"开始分割句子，原文长度: {len(full_text)} 字符")

    # 第1步：去除页眉页脚噪音
    text = _clean_header_footer_noise(full_text)

    # 第2步：移除参考文献段落
    text = _remove_references_section(text)
    logger.debug(f"移除参考文献后文本长度: {len(text)} 字符")

    # 第3步：合并跨行文本
    text = _merge_broken_lines(text)
    logger.debug(f"合并跨行后文本长度: {len(text)} 字符")

    # 第3.5步：在编号小节标题前插入句子边界
    text = _insert_section_boundaries(text)

    # 第4步：分割句子
    raw_sentences = _split_by_sentence_boundaries(text)

    # 第5步：重新计算精确位置
    positioned = _calculate_positions(raw_sentences, text)

    # 第6步：过滤空白句和过短的噪音（如纯数字、单个词）
    filtered: list[tuple[str, int, int]] = []
    for sent_text, start, end in positioned:
        # 去除纯空白
        if not sent_text.strip():
            continue
        # 去除过短的片段（少于 5 个字符，可能是噪音）
        if len(sent_text.strip()) < 5:
            continue
        filtered.append((sent_text, start, end))

    # 第7步：构建 Sentence 对象，填充上下文
    sentences: list[Sentence] = []
    for idx, (sent_text, start, end) in enumerate(filtered):
        prev_text = filtered[idx - 1][0] if idx > 0 else ""
        next_text = filtered[idx + 1][0] if idx < len(filtered) - 1 else ""

        sentences.append(Sentence(
            text=sent_text,
            index=idx,
            start_pos=start,
            end_pos=end,
            prev_sentence=prev_text,
            next_sentence=next_text,
        ))

    logger.info(f"句子分割完成，共 {len(sentences)} 个句子")
    return sentences
