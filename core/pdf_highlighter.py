"""PDF 高亮标注模块

职责：
- 在原 PDF 中定位评论句文本并高亮（黄色）
- 在参考文献区定位被评文献并高亮（绿色）

策略：
- 评论句高亮：短片段分割搜索（优先）→ 去引用标记分割搜索 → 关键词精准定位
- 被评文献高亮：从评论句提取引用编号 → 匹配参考文献 → 在参考文献区定位
"""

import logging
import re
from typing import TYPE_CHECKING

import fitz  # PyMuPDF

if TYPE_CHECKING:
    from core.llm_analyzer import CommentRecord
    from core.ref_parser import Reference

logger = logging.getLogger(__name__)

# ── 颜色常量 ──────────────────────────────────────────────────────
COLOR_SENTENCE = (1, 1, 0)        # 黄色 — 评论句
COLOR_REFERENCE = (0.6, 1, 0.6)   # 浅绿色 — 被评文献（参考文献区）


# ── 工具函数 ──────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """规范化文本：合并多余空白、统一标点"""
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _remove_citation_marks(text: str) -> str:
    """去掉引用标记 [数字] [数字,数字] [数字-数字] 等"""
    return re.sub(r'\[\d+(?:[,，\-–~～]\d+)*\]', '', text)


def _split_into_segments(text: str, min_len: int = 8) -> list[str]:
    """将文本按标点分割为短片段，用于提高 search_for 命中率

    长句子在 PDF 中跨行时 search_for 经常失败，
    分割为短片段后逐段搜索，命中率大幅提高。
    """
    # 按中英文标点分割
    parts = re.split(r'[，。；！？,;!?]', text)
    segments = []
    for p in parts:
        p = p.strip()
        if len(p) >= min_len:
            segments.append(p)
    return segments


def _extract_citation_numbers(sentence: str) -> list[int]:
    """从评论句中提取引用编号，如 [1,2] → [1, 2]，[3-5] → [3, 4, 5]"""
    numbers = set()
    # 匹配 [数字] [数字,数字] [数字-数字] 等
    for m in re.finditer(r'\[(\d+(?:[,，\-–~～]\d+)*)\]', sentence):
        content = m.group(1)
        # 拆分逗号
        for part in re.split(r'[,，]', content):
            part = part.strip()
            # 处理范围 3-5 → 3,4,5
            range_match = re.match(r'(\d+)\s*[\-–~～]\s*(\d+)', part)
            if range_match:
                start, end = int(range_match.group(1)), int(range_match.group(2))
                for n in range(start, end + 1):
                    numbers.add(n)
            elif part.isdigit():
                numbers.add(int(part))
    return sorted(numbers)


def _add_highlight(page: fitz.Page, rects: list[fitz.Rect], color: tuple) -> bool:
    """安全地添加高亮注释"""
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
    """合并 y 坐标相近的矩形（同一行的多个片段合并为一个）"""
    if not rects:
        return []
    # 按 y0 排序
    sorted_rects = sorted(rects, key=lambda r: (r.y0, r.x0))
    merged = [sorted_rects[0]]
    for r in sorted_rects[1:]:
        last = merged[-1]
        # 同一行（y 坐标接近）则扩展
        if abs(r.y0 - last.y0) < y_tolerance:
            merged[-1] = fitz.Rect(
                min(last.x0, r.x0), min(last.y0, r.y0),
                max(last.x1, r.x1), max(last.y1, r.y1),
            )
        else:
            merged.append(r)
    return merged


# ── 评论句定位 ────────────────────────────────────────────────────

def _search_segments_on_page(page: fitz.Page, segments: list[str]) -> list[fitz.Rect]:
    """在页面上搜索多个短片段，汇总所有命中的矩形"""
    all_rects = []
    for seg in segments:
        rects = page.search_for(seg)
        if rects:
            all_rects.extend(rects)
    return all_rects


def _find_sentence_rects(
    page: fitz.Page,
    sentence: str,
    marker: str = "",
    author: str = "",
    year: str = "",
) -> list[fitz.Rect]:
    """定位评论句在页面上的位置

    三级策略：
    1. 将原句分割为短片段搜索
    2. 去引用标记后分割搜索
    3. 用标志词+作者+年份等关键词精准定位
    """
    # ── 策略1：原句短片段搜索 ──
    # 先尝试完整句子
    rects = page.search_for(sentence)
    if rects:
        return rects

    # 按标点分割为短片段搜索
    segments = _split_into_segments(sentence, min_len=8)
    if segments:
        rects = _search_segments_on_page(page, segments)
        if rects:
            return _merge_nearby_rects(rects)

    # ── 策略2：去引用标记后短片段搜索 ──
    cleaned = _remove_citation_marks(sentence).strip()
    cleaned = _normalize_text(cleaned)

    rects = page.search_for(cleaned)
    if rects:
        return rects

    segments_cleaned = _split_into_segments(cleaned, min_len=6)
    if segments_cleaned:
        rects = _search_segments_on_page(page, segments_cleaned)
        if rects:
            return _merge_nearby_rects(rects)

    # ── 策略3：关键词精准定位 ──
    key_terms = _build_key_terms(sentence, marker, author, year)
    all_rects = []
    for term in key_terms:
        found = page.search_for(term)
        all_rects.extend(found)

    if not all_rects:
        return []

    # 用关键词 bounding box 确定句子所在行范围，然后取覆盖区域内的所有文本行
    min_y = min(r.y0 for r in all_rects)
    max_y = max(r.y1 for r in all_rects)

    # 获取该区域内的文本行矩形
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    line_rects = []
    for block in blocks.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            lr = fitz.Rect(line["bbox"])
            if lr.y0 >= min_y - 2 and lr.y1 <= max_y + 2:
                line_rects.append(lr)

    return line_rects if line_rects else all_rects


def _build_key_terms(sentence: str, marker: str, author: str, year: str) -> list[str]:
    """从评论句和记录信息构建搜索关键词列表"""
    terms = []
    cleaned = _remove_citation_marks(sentence)

    # 1. 标志词（来自 record）
    if marker and len(marker) >= 2:
        terms.append(marker)

    # 2. 被评作者
    if author and len(author) >= 2:
        terms.append(author)

    # 3. 年份
    if year and len(year) == 4:
        terms.append(year)

    # 4. 较长的连续中文片段（8-15字）
    cn_segments = re.findall(r'[\u4e00-\u9fff]{8,15}', cleaned)
    terms.extend(cn_segments[:2])

    # 5. 较长的英文短语（至少两个单词）
    en_segments = re.findall(r'[a-zA-Z]{4,}(?:\s+[a-zA-Z]{3,}){1,4}', cleaned)
    terms.extend(en_segments[:2])

    # 6. 如果以上都没有，取句子前20个字符
    if not terms:
        fallback = cleaned.strip()[:20]
        if len(fallback) >= 6:
            terms.append(fallback)

    return terms


# ── 参考文献定位 ──────────────────────────────────────────────────

def _find_reference_rects(page: fitz.Page, ref_text: str) -> list[fitz.Rect]:
    """在页面上定位一条参考文献的位置

    参考文献条目通常较长且跨行，使用前段文本搜索定位起始位置，
    然后通过行间距扩展到完整条目。
    """
    if not ref_text:
        return []

    # 清理换行
    ref_clean = _normalize_text(ref_text)

    # 方法1：尝试完整搜索（短条目可能成功）
    rects = page.search_for(ref_clean)
    if rects:
        return rects

    # 方法2：取前部片段搜索定位（作者+标题部分，通常最具辨识度）
    # 按句号分割，取第一段（作者+标题）
    first_parts = re.split(r'[．.]', ref_clean)
    for part in first_parts:
        part = part.strip()
        if len(part) >= 10:
            rects = page.search_for(part)
            if rects:
                # 找到起始位置后，扩展到整个参考文献条目
                return _expand_to_full_ref(page, rects)

    # 方法3：短片段搜索
    segments = _split_into_segments(ref_clean, min_len=10)
    if segments:
        # 取前3个最长的片段
        segments.sort(key=len, reverse=True)
        rects = _search_segments_on_page(page, segments[:3])
        if rects:
            return _merge_nearby_rects(rects)

    # 方法4：用作者名+年份定位
    # 从 raw_text 提取前几个字符（通常是作者名）
    author_part = ref_clean[:20]
    if author_part:
        rects = page.search_for(author_part)
        if rects:
            return _expand_to_full_ref(page, rects)

    return []


def _expand_to_full_ref(page: fitz.Page, anchor_rects: list[fitz.Rect]) -> list[fitz.Rect]:
    """从锚点矩形扩展到完整的参考文献条目（通常2-3行）"""
    if not anchor_rects:
        return []

    anchor_y0 = min(r.y0 for r in anchor_rects)
    anchor_y1 = max(r.y1 for r in anchor_rects)
    line_height = anchor_y1 - anchor_y0 if anchor_y1 > anchor_y0 else 12

    # 参考文献一般2-4行，向下扩展
    expand_y_bottom = anchor_y1 + line_height * 3

    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    line_rects = []
    for block in blocks.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            lr = fitz.Rect(line["bbox"])
            if lr.y0 >= anchor_y0 - 2 and lr.y0 <= expand_y_bottom:
                line_rects.append(lr)

    return line_rects if line_rects else anchor_rects


# ── 主函数 ────────────────────────────────────────────────────────

def highlight_sentences(
    input_pdf_path: str,
    output_pdf_path: str,
    records: list["CommentRecord"],
    references: list["Reference"] | None = None,
    progress_callback=None,
) -> int:
    """在 PDF 中高亮标记评论句和被评文献

    对每条评论句记录：
    1. 黄色高亮评论句本身
    2. 绿色高亮参考文献区中对应的被评文献条目

    Args:
        input_pdf_path: 输入 PDF 路径
        output_pdf_path: 输出 PDF 路径
        records: 评论句记录列表（含被评文献信息）
        references: 参考文献列表（用于定位被评文献在参考文献区的原文）
        progress_callback: 进度回调

    Returns:
        成功高亮的评论句数量
    """
    doc = fitz.open(input_pdf_path)
    highlighted_count = 0

    # 构建参考文献编号→Reference 的映射
    ref_map: dict[int, "Reference"] = {}
    if references:
        for ref in references:
            ref_map[ref.index] = ref

    # 记录已高亮的参考文献编号，避免重复高亮
    highlighted_ref_indices: set[int] = set()

    # 参考文献通常在最后几页，预计算页码范围
    total_pages = doc.page_count
    ref_page_start = max(0, total_pages - 3)  # 参考文献一般在最后3页

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
        # 从评论句中提取引用编号
        citation_nums = _extract_citation_numbers(sentence)

        # 同时通过作者+年份匹配
        if references and not citation_nums:
            from core.ref_parser import find_reference_by_author_year
            matched = find_reference_by_author_year(references, author, year)
            if matched:
                citation_nums = [matched.index]

        for ref_idx in citation_nums:
            if ref_idx in highlighted_ref_indices:
                continue  # 已高亮过，跳过

            ref = ref_map.get(ref_idx)
            if not ref:
                continue

            # 在参考文献区域页面搜索
            ref_found = False
            for page_num in range(ref_page_start, total_pages):
                page = doc[page_num]
                rects = _find_reference_rects(page, ref.raw_text)
                if rects:
                    if _add_highlight(page, rects, COLOR_REFERENCE):
                        ref_found = True
                        highlighted_ref_indices.add(ref_idx)
                        logger.info(f"[被评文献] 第{page_num+1}页高亮参考文献[{ref_idx}]: "
                                    f"{ref.raw_text[:40]}...")
                    break

            if not ref_found:
                logger.warning(f"[被评文献] 未找到参考文献[{ref_idx}]: {ref.raw_text[:50]}...")

    # ━━ 保存 ━━
    try:
        doc.save(output_pdf_path)
    except Exception as e:
        logger.warning(f"PDF 高亮保存失败，尝试增量保存: {e}")
        try:
            doc.save(output_pdf_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        except Exception as e2:
            logger.error(f"PDF 高亮保存彻底失败，跳过高亮步骤: {e2}")
            import shutil
            doc.close()
            shutil.copy2(input_pdf_path, output_pdf_path)
            return 0
    doc.close()

    logger.info(f"高亮完成: {highlighted_count}/{len(records)} 条评论句, "
                f"{len(highlighted_ref_indices)} 条被评文献")
    return highlighted_count
