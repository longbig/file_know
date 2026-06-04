"""PDF 高亮标注模块

高亮三类内容：
1. 评论句（黄色）
2. 被评文献参考文献条目（绿色）
3. 施评文献标题+作者区（蓝色）
"""

import logging
import re
from typing import TYPE_CHECKING

import fitz  # PyMuPDF

if TYPE_CHECKING:
    from core.llm_analyzer import CommentRecord
    from core.pdf_parser import PaperMetadata
    from core.ref_parser import Reference

logger = logging.getLogger(__name__)

COLOR_SENTENCE = (1, 1, 0)        # 黄色 — 评论句
COLOR_REFERENCE = (0.6, 1, 0.6)   # 浅绿色 — 被评文献
COLOR_REVIEWING = (0.6, 0.8, 1.0) # 浅蓝色 — 施评文献


def _normalize_text(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def _clean_for_search(text: str) -> str:
    """清理文本中可能导致 PDF 搜索失败的特殊字符"""
    text = text.replace('\u201c', '"').replace('\u201d', '"')   # 智能双引号
    text = text.replace('\u2018', "'").replace('\u2019', "'")   # 智能单引号
    text = text.replace('\u2011', '-')   # 非断行连字符
    text = text.replace('\u00ad', '')    # 软连字符
    text = text.replace('\u2013', '-').replace('\u2014', '-')   # en/em dash
    return text


def _remove_citation_marks(text: str) -> str:
    return re.sub(r'\[\d+(?:[,，\-–~～]\d+)*\]', '', text)


def _split_into_segments(text: str, min_len: int = 8) -> list[str]:
    parts = re.split(r'[，。；！？,;!?]', text)
    return [p.strip() for p in parts if len(p.strip()) >= min_len]


def _extract_citation_numbers(sentence: str) -> list[int]:
    numbers = set()
    for m in re.finditer(r'\[(\d+(?:[,，\-–~～]\d+)*)\]', sentence):
        content = m.group(1)
        for part in re.split(r'[,，]', content):
            part = part.strip()
            range_match = re.match(r'(\d+)\s*[\-–~～]\s*(\d+)', part)
            if range_match:
                for n in range(int(range_match.group(1)), int(range_match.group(2)) + 1):
                    numbers.add(n)
            elif part.isdigit():
                numbers.add(int(part))
    return sorted(numbers)


def _add_highlight(page: fitz.Page, rects: list[fitz.Rect], color: tuple) -> bool:
    if not rects:
        return False
    try:
        highlight = page.add_highlight_annot(rects)
        highlight.set_colors(stroke=color)
        highlight.update()
        return True
    except Exception as e:
        logger.warning(f"添加高亮失败: {e}")
        return False


def _merge_nearby_rects(rects: list[fitz.Rect], y_tolerance: float = 3.0) -> list[fitz.Rect]:
    """合并 y 坐标相近且 x 范围重叠/相邻的矩形"""
    if not rects:
        return []
    sorted_rects = sorted(rects, key=lambda r: (r.y0, r.x0))
    merged = [sorted_rects[0]]
    for r in sorted_rects[1:]:
        last = merged[-1]
        # 同一行（y 接近）且 x 范围有重叠或相邻（间距 < 50pt）
        if abs(r.y0 - last.y0) < y_tolerance and r.x0 <= last.x1 + 50:
            merged[-1] = fitz.Rect(
                min(last.x0, r.x0), min(last.y0, r.y0),
                max(last.x1, r.x1), max(last.y1, r.y1),
            )
        else:
            merged.append(r)
    return merged


# ── 分栏检测 ──────────────────────────────────────────────────────

def _detect_columns(page: fitz.Page) -> list[tuple[float, float]]:
    """检测页面是否为多栏布局

    通过分析文本行的 x 坐标分布来判断。
    如果存在明显的左右分栏（中间有间隙），返回各栏的 x 范围。

    Returns:
        [(x_left, x_right), ...] 每栏的 x 范围。
        单栏返回 [(page_x0, page_x1)]
    """
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    x_starts = []
    x_ends = []

    for block in blocks.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            bbox = line["bbox"]
            line_width = bbox[2] - bbox[0]
            # 忽略太短的行（标题、页码等）和太宽的行（跨栏标题）
            if line_width < 50 or line_width > page.rect.width * 0.8:
                continue
            x_starts.append(bbox[0])
            x_ends.append(bbox[2])

    if not x_starts:
        return [(page.rect.x0, page.rect.x1)]

    # 分析 x_start 的聚类：如果有两个明显的起始 x 聚类，说明是双栏
    x_starts_sorted = sorted(x_starts)
    page_mid = page.rect.width / 2

    # 统计左半部分和右半部分的行数
    left_starts = [x for x in x_starts_sorted if x < page_mid - 20]
    right_starts = [x for x in x_starts_sorted if x > page_mid - 20]

    if len(left_starts) > 5 and len(right_starts) > 5:
        # 双栏布局
        left_x0 = min(left_starts)
        left_x1 = max(e for s, e in zip(x_starts, x_ends) if s < page_mid - 20)
        right_x0 = min(right_starts)
        right_x1 = max(e for s, e in zip(x_starts, x_ends) if s > page_mid - 20)

        # 确认两栏之间有间隙（至少 10pt）
        if right_x0 - left_x1 > 10:
            return [(left_x0, left_x1), (right_x0, right_x1)]

    return [(page.rect.x0, page.rect.x1)]


def _determine_column(columns: list[tuple[float, float]], x: float) -> int:
    """判断 x 坐标属于哪一栏（返回栏索引）"""
    for i, (col_x0, col_x1) in enumerate(columns):
        if col_x0 - 10 <= x <= col_x1 + 10:
            return i
    # 默认取最近的栏
    min_dist = float('inf')
    best = 0
    for i, (col_x0, col_x1) in enumerate(columns):
        mid = (col_x0 + col_x1) / 2
        dist = abs(x - mid)
        if dist < min_dist:
            min_dist = dist
            best = i
    return best


def _filter_rects_by_column(
    rects: list[fitz.Rect],
    columns: list[tuple[float, float]],
    anchor_col: int,
) -> list[fitz.Rect]:
    """过滤矩形，只保留属于指定栏的"""
    if len(columns) <= 1:
        return rects  # 单栏不过滤
    col_x0, col_x1 = columns[anchor_col]
    return [r for r in rects if r.x0 >= col_x0 - 15 and r.x1 <= col_x1 + 15]


def _get_line_rects_in_range(
    page: fitz.Page,
    y0: float,
    y1: float,
    column_x: tuple[float, float] | None = None,
) -> list[fitz.Rect]:
    """获取页面上 y 范围内的文本行矩形

    Args:
        page: 页面
        y0, y1: y 坐标范围
        column_x: 如果指定，只返回该栏 x 范围内的行 (x_left, x_right)
    """
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    line_rects = []
    for block in blocks.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            lr = fitz.Rect(line["bbox"])
            if lr.y0 >= y0 - 2 and lr.y1 <= y1 + 2:
                # 如果指定了栏范围，过滤不在该栏的行
                if column_x:
                    col_x0, col_x1 = column_x
                    if lr.x0 < col_x0 - 15 or lr.x1 > col_x1 + 15:
                        continue
                line_rects.append(lr)
    return line_rects


# ── 锚点搜索 ────────────────────────────────────────────────────

def _search_anchor(
    page: fitz.Page,
    text: str,
    from_start: bool = True,
) -> list[fitz.Rect]:
    """搜索句子首部或尾部的短片段，作为定位锚点

    策略：
    1. 从首/尾取不同长度的片段尝试
    2. 跳过开头特殊字符（引号等）
    3. 如果首/尾片段都搜不到，从句子中按空格/标点分割，
       跳过前几个/后几个词，取中间偏首/尾的片段
    """
    text = text.strip()
    if not text:
        return []

    candidates: list[str] = []

    # ── 基本候选：不同长度的首/尾片段 ──
    for length in [50, 35, 25, 18, 12]:
        if from_start:
            seg = text[:length].strip()
        else:
            seg = text[-length:].strip()
        if len(seg) >= 8:
            candidates.append(seg)

    # ── 句首额外策略：跳过开头的引号/标点/特殊字符 ──
    if from_start:
        # 跳到第一个连续字母段
        m = re.search(r'[a-zA-Z\u4e00-\u9fff]{3,}', text)
        if m and m.start() > 0:
            alt_text = text[m.start():]
            for length in [40, 30, 20]:
                seg = alt_text[:length].strip()
                if len(seg) >= 8 and seg not in candidates:
                    candidates.append(seg)

    # ── 跳词策略：跳过前/后 N 个词，从中间偏首/尾取片段 ──
    # 这能绕过断行连字符（如 "water-\nin-salt"）导致的匹配失败
    words = text.split()
    if from_start and len(words) > 6:
        for skip in [2, 3, 4, 5]:
            remaining = ' '.join(words[skip:])
            for length in [40, 30, 20]:
                seg = remaining[:length].strip()
                if len(seg) >= 10 and seg not in candidates:
                    candidates.append(seg)
    elif not from_start and len(words) > 6:
        for skip in [2, 3, 4]:
            remaining = ' '.join(words[:-skip])
            for length in [40, 30, 20]:
                seg = remaining[-length:].strip()
                if len(seg) >= 10 and seg not in candidates:
                    candidates.append(seg)

    for seg in candidates:
        rects = page.search_for(seg)
        if rects:
            return rects
        # 尝试清理特殊字符后搜索
        cleaned = _clean_for_search(seg)
        if cleaned != seg:
            rects = page.search_for(cleaned)
            if rects:
                return rects

    return []


def _expand_between_anchors(
    page: fitz.Page,
    start_rects: list[fitz.Rect],
    end_rects: list[fitz.Rect],
    columns: list[tuple[float, float]],
) -> list[fitz.Rect]:
    """在首尾锚点之间扩展高亮，支持跨栏

    - 同栏：高亮 start_y 到 end_y 之间所有行
    - 跨栏（左→右）：左栏从 start_y 到栏底 + 右栏从栏顶到 end_y
    """
    start_col = _determine_column(columns, start_rects[0].x0)
    end_col = _determine_column(columns, end_rects[-1].x0)

    if start_col == end_col:
        # 同栏：高亮首尾之间所有行
        column_x = columns[start_col] if len(columns) > 1 else None
        y0 = min(r.y0 for r in start_rects)
        y1 = max(r.y1 for r in end_rects)
        line_rects = _get_line_rects_in_range(page, y0, y1, column_x)
        return line_rects or start_rects + end_rects
    else:
        # 跨栏：start_col → end_col
        all_rects: list[fitz.Rect] = []

        # 第一部分：起始栏从 start_y 到栏底
        col1_x = columns[start_col] if len(columns) > 1 else None
        y0_start = min(r.y0 for r in start_rects)
        part1 = _get_line_rects_in_range(
            page, y0_start, page.rect.height, col1_x,
        )
        all_rects.extend(part1)

        # 中间栏（三栏以上布局时）
        for col_idx in range(start_col + 1, end_col):
            mid_x = columns[col_idx]
            mid_rects = _get_line_rects_in_range(
                page, 0, page.rect.height, mid_x,
            )
            all_rects.extend(mid_rects)

        # 最后部分：结束栏从栏顶到 end_y
        col2_x = columns[end_col] if len(columns) > 1 else None
        y1_end = max(r.y1 for r in end_rects)
        part2 = _get_line_rects_in_range(page, 0, y1_end, col2_x)
        all_rects.extend(part2)

        return all_rects or start_rects + end_rects


# ── 评论句定位 ────────────────────────────────────────────────────

def _find_sentence_rects(
    page: fitz.Page,
    sentence: str,
    marker: str = "",
    author: str = "",
    year: str = "",
) -> list[fitz.Rect]:
    """定位评论句在页面上的位置

    策略（逐级降级）：
    1. 完整句子搜索
    2. 去引用标记后完整搜索
    3. 首尾锚定：搜索句子首部和尾部短片段，高亮两者之间所有行（支持跨栏）
    4. 分段搜索 + 范围扩展：找到部分片段后，扩展到完整 y 范围
    5. 关键词定位（兜底）
    """
    # 检测分栏
    columns = _detect_columns(page)

    # 策略1：完整句子
    rects = page.search_for(sentence)
    if rects:
        return rects

    # 策略2：去引用标记后完整搜索
    cleaned = _normalize_text(_remove_citation_marks(sentence))
    rects = page.search_for(cleaned)
    if rects:
        return rects

    # 也试试清理特殊字符后搜索
    cleaned_search = _clean_for_search(cleaned)
    if cleaned_search != cleaned:
        rects = page.search_for(cleaned_search)
        if rects:
            return rects

    # 策略3：首尾锚定 — 找到句首和句尾的位置，高亮之间所有行
    start_rects = _search_anchor(page, cleaned, from_start=True)
    end_rects = _search_anchor(page, cleaned, from_start=False)

    if start_rects and end_rects:
        result = _expand_between_anchors(page, start_rects, end_rects, columns)
        if result:
            return result

    # 只有句首锚点 — 向下扩展估算行数
    if start_rects:
        anchor_col = _determine_column(columns, start_rects[0].x0)
        column_x = columns[anchor_col] if len(columns) > 1 else None
        y0 = min(r.y0 for r in start_rects)
        line_h = max((r.y1 - r.y0 for r in start_rects), default=12)
        # 根据句子长度估算行数（每行约 80 字符）
        est_lines = max(3, len(cleaned) // 80 + 2)
        y1 = y0 + line_h * est_lines
        line_rects = _get_line_rects_in_range(page, y0, y1, column_x)
        if line_rects:
            return line_rects

    # 只有句尾锚点 — 向上扩展
    if end_rects:
        anchor_col = _determine_column(columns, end_rects[0].x0)
        column_x = columns[anchor_col] if len(columns) > 1 else None
        y1 = max(r.y1 for r in end_rects)
        line_h = max((r.y1 - r.y0 for r in end_rects), default=12)
        est_lines = max(3, len(cleaned) // 80 + 2)
        y0 = y1 - line_h * est_lines
        line_rects = _get_line_rects_in_range(page, y0, y1, column_x)
        if line_rects:
            return line_rects

    # 策略4：分段搜索 + 范围扩展
    segments = _split_into_segments(cleaned, min_len=10)
    if segments:
        segments.sort(key=len, reverse=True)
        all_rects: list[fitz.Rect] = []
        for seg in segments:
            found = page.search_for(seg)
            if not found:
                found = page.search_for(_clean_for_search(seg))
            all_rects.extend(found)
        if all_rects:
            anchor_col = _determine_column(columns, all_rects[0].x0)
            filtered = _filter_rects_by_column(all_rects, columns, anchor_col)
            if filtered:
                # 用找到的片段确定 y 范围，获取该范围内所有行
                column_x = columns[anchor_col] if len(columns) > 1 else None
                y0 = min(r.y0 for r in filtered)
                y1 = max(r.y1 for r in filtered)
                line_rects = _get_line_rects_in_range(page, y0, y1, column_x)
                return line_rects or _merge_nearby_rects(filtered)
            return _merge_nearby_rects(all_rects)

    # 策略5：关键词精准定位（兜底）
    # 要求至少 2 个关键词在相近 y 位置匹配，防止单词误匹配
    key_terms = _build_key_terms(sentence, marker, author, year)
    term_rects_map: dict[str, list[fitz.Rect]] = {}
    for term in key_terms:
        found = page.search_for(term)
        if found:
            term_rects_map[term] = found

    if len(term_rects_map) < 2:
        # 只有一个关键词匹配，可信度太低，跳过
        return []

    # 找到所有关键词中，至少两个关键词 y 坐标接近（<50pt）的位置
    all_term_rects = []
    for rects_list in term_rects_map.values():
        all_term_rects.extend(rects_list)

    # 用第一个关键词的位置作为锚点，检查附近是否有其他关键词
    best_rects: list[fitz.Rect] = []
    first_term_key = list(term_rects_map.keys())[0]
    for anchor_r in term_rects_map[first_term_key]:
        nearby = [anchor_r]
        for other_term, other_rects in term_rects_map.items():
            if other_term == first_term_key:
                continue
            for r in other_rects:
                if abs(r.y0 - anchor_r.y0) < 80:  # 80pt ≈ 5-6行
                    nearby.append(r)
                    break
        if len(nearby) >= 2 and len(nearby) > len(best_rects):
            best_rects = nearby

    if not best_rects:
        return []

    anchor_col = _determine_column(columns, best_rects[0].x0)
    column_x = columns[anchor_col] if len(columns) > 1 else None

    min_y = min(r.y0 for r in best_rects)
    max_y = max(r.y1 for r in best_rects)
    line_rects = _get_line_rects_in_range(page, min_y, max_y, column_x=column_x)
    return line_rects if line_rects else best_rects


def _build_key_terms(sentence: str, marker: str, author: str, year: str) -> list[str]:
    terms = []
    cleaned = _remove_citation_marks(sentence)

    # 优先用较长的英文短语（至少3个单词）
    en_segments = re.findall(r'[a-zA-Z]{4,}(?:\s+[a-zA-Z]{3,}){2,4}', cleaned)
    terms.extend(en_segments[:2])

    # 较长的中文片段
    cn_segments = re.findall(r'[\u4e00-\u9fff]{8,15}', cleaned)
    terms.extend(cn_segments[:2])

    # 作者+年份组合（比单独的标志词更精确）
    if author and len(author) >= 3 and year:
        terms.append(author)
        terms.append(year)
    elif marker and len(marker) >= 4:
        terms.append(marker)

    # 兜底：句子前30个字符
    if not terms:
        fallback = cleaned.strip()[:30]
        if len(fallback) >= 8:
            terms.append(fallback)

    return terms


# ── 参考文献定位 ──────────────────────────────────────────────────

def _find_ref_by_number_and_author(
    page: fitz.Page,
    ref_index: int,
    first_author_surname: str,
    year: str,
) -> list[fitz.Rect]:
    """通过编号+作者姓氏定位参考文献条目

    PDF 中参考文献格式通常为：
      4.  Li, W., Dahn, J.R., ...
      [4] Li W, Dahn JR, ...
    用编号+作者姓氏组合定位，比用 raw_text 更可靠。
    """
    # 尝试多种编号格式
    number_patterns = [
        f"{ref_index}.",
        f"[{ref_index}]",
        f"{ref_index}\t",
        f"\t{ref_index}.",
        f"\t{ref_index}\t",
    ]

    for num_pat in number_patterns:
        # 先搜索编号
        num_rects = page.search_for(num_pat)
        if not num_rects:
            continue

        for num_rect in num_rects:
            # 在编号附近（同行或下一行）搜索作者姓氏
            search_area = fitz.Rect(
                num_rect.x0, num_rect.y0 - 3,
                page.rect.width, num_rect.y1 + 20
            )
            area_text = page.get_text("text", clip=search_area)
            if first_author_surname.lower() in area_text.lower():
                # 找到了，扩展到完整参考文献条目
                return _expand_to_full_ref(page, [num_rect])

    # 备用：直接搜索作者姓氏+年份
    if first_author_surname and year:
        surname_rects = page.search_for(first_author_surname)
        for sr in surname_rects:
            # 检查附近是否有年份
            ctx = fitz.Rect(sr.x0 - 10, sr.y0 - 3, page.rect.width, sr.y1 + 50)
            ctx_text = page.get_text("text", clip=ctx)
            if year in ctx_text:
                return _expand_to_full_ref(page, [sr])

    return []


def _find_reference_rects(
    page: fitz.Page,
    ref_text: str,
    ref_index: int = 0,
    first_author_surname: str = "",
    year: str = "",
) -> list[fitz.Rect]:
    """定位参考文献条目"""
    if not ref_text and not first_author_surname:
        return []

    ref_clean = _normalize_text(ref_text)

    # 方法1：完整搜索
    rects = page.search_for(ref_clean)
    if rects:
        return rects

    # 方法2：编号+作者定位（最可靠）
    if ref_index > 0 and first_author_surname:
        rects = _find_ref_by_number_and_author(page, ref_index, first_author_surname, year)
        if rects:
            return rects

    # 方法3：按句号分割取第一段
    first_parts = re.split(r'[．.]', ref_clean)
    for part in first_parts:
        part = part.strip()
        if len(part) >= 10:
            rects = page.search_for(part)
            if rects:
                return _expand_to_full_ref(page, rects)

    # 方法4：短片段搜索
    segments = _split_into_segments(ref_clean, min_len=10)
    if segments:
        segments.sort(key=len, reverse=True)
        all_rects = []
        for seg in segments[:3]:
            found = page.search_for(seg)
            all_rects.extend(found)
        if all_rects:
            return _merge_nearby_rects(all_rects)

    return []


def _expand_to_full_ref(page: fitz.Page, anchor_rects: list[fitz.Rect]) -> list[fitz.Rect]:
    """从锚点扩展到完整参考文献条目（2-4行）

    支持分栏：只扩展同一栏的内容。
    """
    if not anchor_rects:
        return []

    columns = _detect_columns(page)
    anchor_col = _determine_column(columns, anchor_rects[0].x0)
    column_x = columns[anchor_col] if len(columns) > 1 else None

    anchor_y0 = min(r.y0 for r in anchor_rects)
    anchor_y1 = max(r.y1 for r in anchor_rects)
    line_height = max(anchor_y1 - anchor_y0, 12)
    expand_y_bottom = anchor_y1 + line_height * 3

    return _get_line_rects_in_range(page, anchor_y0, expand_y_bottom, column_x=column_x) or anchor_rects


# ── 施评文献定位 ──────────────────────────────────────────────────

def _find_reviewing_paper_rects(
    doc: fitz.Document,
    title: str,
    first_author: str,
) -> tuple[int, list[fitz.Rect]]:
    """定位施评文献标题和作者区域（通常在第1页）

    分别搜索标题和作者，合并两者的矩形区域。

    Returns:
        (page_num, rects)
    """
    for page_num in range(min(3, doc.page_count)):
        page = doc[page_num]
        all_rects = []

        # 搜索标题（可能含特殊连字符，用多个短片段搜索）
        if title:
            # 取标题中不含连字符的单词片段搜索
            title_words = re.split(r'[-\u2011\s]+', title)
            title_segments = []
            seg = []
            for w in title_words:
                seg.append(w)
                if len(' '.join(seg)) >= 15:
                    title_segments.append(' '.join(seg))
                    seg = []
            if seg and len(' '.join(seg)) >= 5:
                title_segments.append(' '.join(seg))

            title_rects_all = []
            for ts in title_segments[:3]:  # 前3段足够
                found = page.search_for(ts)
                title_rects_all.extend(found)

            if title_rects_all:
                # 只取页面上半部分（y < 300）的结果
                title_rects_top = [r for r in title_rects_all if r.y0 < 300]
                if title_rects_top:
                    title_y0 = min(r.y0 for r in title_rects_top)
                    title_y1 = max(r.y1 for r in title_rects_top)
                    title_lines = _get_line_rects_in_range(page, title_y0, title_y1)
                    all_rects.extend(title_lines or title_rects_top)

        # 搜索第一作者（只取页面上半部分的第一个匹配）
        if first_author and len(first_author) >= 4:
            author_rects = page.search_for(first_author)
            # 过滤：只取 y < 300 的第一个匹配
            author_rects_top = [r for r in author_rects if r.y0 < 300]
            if author_rects_top:
                author_y0 = min(r.y0 for r in author_rects_top)
                author_y1 = max(r.y1 for r in author_rects_top)
                line_h = max(author_y1 - author_y0, 12)
                # 作者列表可能跨2行
                author_lines = _get_line_rects_in_range(page, author_y0, author_y1 + line_h)
                all_rects.extend(author_lines or author_rects_top)

        if all_rects:
            return page_num, _merge_nearby_rects(all_rects)

    return -1, []


# ── 主函数 ────────────────────────────────────────────────────────

def highlight_sentences(
    input_pdf_path: str,
    output_pdf_path: str,
    records: list["CommentRecord"],
    references: list["Reference"] | None = None,
    metadata: "PaperMetadata | None" = None,
    progress_callback=None,
) -> int:
    """在 PDF 中高亮标记评论句、被评文献、施评文献

    Args:
        input_pdf_path: 输入 PDF 路径
        output_pdf_path: 输出 PDF 路径
        records: 评论句记录列表
        references: 参考文献列表
        metadata: 施评文献元数据（用于高亮施评文献区域）
        progress_callback: 进度回调

    Returns:
        成功高亮的评论句数量
    """
    doc = fitz.open(input_pdf_path)
    highlighted_count = 0

    ref_map: dict[int, "Reference"] = {}
    if references:
        for ref in references:
            ref_map[ref.index] = ref

    highlighted_ref_indices: set[int] = set()
    total_pages = doc.page_count
    ref_page_start = max(0, total_pages - 5)  # 参考文献一般在最后5页

    # ━━ 0. 高亮施评文献（蓝色）━━
    if metadata:
        title = metadata.title_cn or metadata.title_en or ""
        first_author = metadata.first_author or ""
        if title or first_author:
            page_num, rects = _find_reviewing_paper_rects(doc, title, first_author)
            if rects:
                if _add_highlight(doc[page_num], rects, COLOR_REVIEWING):
                    logger.info(f"[施评文献] 第{page_num+1}页高亮: {title[:40]}")
            else:
                logger.warning(f"[施评文献] 未找到: {title[:50]}")

    for i, record in enumerate(records):
        sentence = record.评论句原文
        marker = record.标志词
        author = record.被评文献.第一作者
        year = record.被评文献.年份

        if progress_callback:
            progress_callback(f"高亮第 {i+1}/{len(records)} 条: {sentence[:30]}...")

        # ━━ 1. 高亮评论句（黄色）━━
        sentence_found = False
        for page_num in range(total_pages):
            page = doc[page_num]
            rects = _find_sentence_rects(page, sentence, marker, author, year)
            if rects:
                if _add_highlight(page, rects, COLOR_SENTENCE):
                    sentence_found = True
                    logger.info(f"[评论句] 第{page_num+1}页高亮: {sentence[:40]}...")
                break

        if sentence_found:
            highlighted_count += 1
        else:
            logger.warning(f"[评论句] 未找到: {sentence[:50]}...")

        # ━━ 2. 高亮被评文献（绿色）━━
        citation_nums = _extract_citation_numbers(sentence)

        if references and not citation_nums:
            from core.ref_parser import find_reference_by_author_year
            matched = find_reference_by_author_year(references, author, year)
            if matched:
                citation_nums = [matched.index]

        for ref_idx in citation_nums:
            if ref_idx in highlighted_ref_indices:
                continue

            ref = ref_map.get(ref_idx)
            if not ref:
                continue

            # 提取第一作者姓氏（逗号前或空格分割的最后一词）
            first_author_surname = ""
            if ref.first_author:
                fa = ref.first_author.strip()
                if ',' in fa:
                    first_author_surname = fa.split(',')[0].strip()
                else:
                    parts = fa.split()
                    first_author_surname = parts[-1] if parts else fa

            ref_found = False
            for page_num in range(ref_page_start, total_pages):
                page = doc[page_num]
                rects = _find_reference_rects(
                    page,
                    ref.raw_text,
                    ref_index=ref_idx,
                    first_author_surname=first_author_surname,
                    year=ref.year,
                )
                if rects:
                    if _add_highlight(page, rects, COLOR_REFERENCE):
                        ref_found = True
                        highlighted_ref_indices.add(ref_idx)
                        logger.info(f"[被评文献] 第{page_num+1}页高亮参考文献[{ref_idx}]")
                    break

            if not ref_found:
                logger.warning(f"[被评文献] 未找到参考文献[{ref_idx}]: {ref.raw_text[:50]}...")

    try:
        doc.save(output_pdf_path)
    except Exception as e:
        logger.warning(f"PDF 高亮保存失败，尝试增量保存: {e}")
        try:
            doc.save(output_pdf_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        except Exception as e2:
            logger.error(f"PDF 高亮保存彻底失败: {e2}")
            import shutil
            doc.close()
            shutil.copy2(input_pdf_path, output_pdf_path)
            return 0
    doc.close()

    logger.info(f"高亮完成: {highlighted_count}/{len(records)} 条评论句, "
                f"{len(highlighted_ref_indices)} 条被评文献")
    return highlighted_count
