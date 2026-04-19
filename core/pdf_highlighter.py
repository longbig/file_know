"""PDF 高亮标注模块

职责：
- 在原 PDF 中定位评论句文本
- 添加黄色高亮注释

注意：学术论文 PDF 中的句子通常跨行，search_for 对长文本和跨行文本
经常失败。采用"关键词定位 + 区域扩展"策略。
"""

import logging
import re

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def _extract_key_terms(sentence: str) -> list[str]:
    """从评论句中提取用于定位的关键词"""
    terms = []

    # 去掉引用标记 [数字]
    cleaned = re.sub(r'\[\d+(?:[,-]\d+)*\]', '', sentence)

    # 提取年份
    year_match = re.search(r'\d{4}', sentence)
    if year_match:
        terms.append(year_match.group(0))

    # 提取中文人名（2-4个汉字 + 等）
    name_match = re.search(r'([\u4e00-\u9fff]{2,4})等', cleaned)
    if name_match:
        terms.append(name_match.group(1) + "等")
    else:
        # 英文姓氏
        en_name = re.search(r'([A-Z][a-z]+)\s+等', cleaned)
        if en_name:
            terms.append(en_name.group(1))

    # 提取标志词（常见的）
    marker_words = [
        '最早', '首次', '首先', '第一次', '率先', '开创',
        'first', 'firstly', 'earliest', 'pioneering',
    ]
    for mw in marker_words:
        if mw in cleaned.lower():
            terms.append(mw)
            break

    # 提取较长的连续中文片段（6-12字）
    cn_segments = re.findall(r'[\u4e00-\u9fff]{6,12}', cleaned)
    terms.extend(cn_segments[:3])

    # 提取较长的英文短语
    en_segments = re.findall(r'[a-zA-Z]{4,}(?:\s+[a-zA-Z]{3,}){1,3}', cleaned)
    terms.extend(en_segments[:2])

    return terms


def _find_sentence_rects(page: fitz.Page, sentence: str) -> list[fitz.Rect]:
    """通过关键词搜索定位句子区域"""
    # 方法1：直接搜索完整句子
    results = page.search_for(sentence)
    if results:
        return results

    # 方法2：去掉引用标记后搜索
    cleaned = re.sub(r'\[\d+(?:[,-]\d+)*\]', '', sentence).strip()
    results = page.search_for(cleaned)
    if results:
        return results

    # 方法3：关键词定位
    key_terms = _extract_key_terms(sentence)
    all_rects = []

    for term in key_terms:
        rects = page.search_for(term)
        all_rects.extend(rects)

    if not all_rects:
        return []

    # 计算所有关键词的 bounding box，确定句子所在区域
    min_x = min(r.x0 for r in all_rects)
    min_y = min(r.y0 for r in all_rects)
    max_x = max(r.x1 for r in all_rects)
    max_y = max(r.y1 for r in all_rects)

    # 使用 text blocks 精确定位
    # 获取覆盖区域内的所有文本块
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    sentence_rects = []

    for block in blocks.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_rect = fitz.Rect(line["bbox"])
            # 检查行是否在关键词区域内（y 坐标重叠）
            if line_rect.y0 >= min_y - 5 and line_rect.y1 <= max_y + 5:
                sentence_rects.append(line_rect)

    return sentence_rects if sentence_rects else all_rects


def highlight_sentences(
    input_pdf_path: str,
    output_pdf_path: str,
    sentences: list[str],
    progress_callback=None,
) -> int:
    """在 PDF 中高亮标记指定句子

    Args:
        input_pdf_path: 输入 PDF 路径
        output_pdf_path: 输出 PDF 路径
        sentences: 需要高亮的句子列表
        progress_callback: 进度回调

    Returns:
        成功高亮的句子数量
    """
    doc = fitz.open(input_pdf_path)
    highlighted_count = 0

    for i, sentence in enumerate(sentences):
        if progress_callback:
            progress_callback(f"高亮句子 {i+1}/{len(sentences)}...")

        found = False
        for page_num in range(doc.page_count):
            page = doc[page_num]
            rects = _find_sentence_rects(page, sentence)

            if rects:
                try:
                    highlight = page.add_highlight_annot(rects)
                    highlight.set_colors(stroke=(1, 1, 0))  # 黄色
                    highlight.update()
                    found = True
                    logger.info(f"在第 {page_num+1} 页高亮句子: {sentence[:30]}...")
                except Exception as e:
                    logger.warning(f"添加高亮失败: {e}")
                break

        if found:
            highlighted_count += 1
        else:
            logger.warning(f"未找到句子位置: {sentence[:50]}...")

    doc.save(output_pdf_path)
    doc.close()

    return highlighted_count
