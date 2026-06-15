"""PDF 文本提取模块

职责：
- 从 PDF 中提取全文文本（处理双栏排版）
- 保留每段文字的页码和坐标位置（用于后续高亮）
- 提取施评文献的元数据（作者、期刊、机构等）
"""

import logging
import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# RapidOCR 懒加载（避免每次 import 都初始化模型）
_ocr_engine = None

def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
            logger.info("RapidOCR 初始化成功")
        except ImportError:
            logger.warning("rapidocr-onnxruntime 未安装，扫描版 PDF 将无法识别")
    return _ocr_engine


@dataclass
class TextBlock:
    """文本块，包含位置信息"""
    text: str
    page_num: int  # 0-indexed
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)


@dataclass
class PaperMetadata:
    """施评文献元数据"""
    title_cn: str = ""
    title_en: str = ""
    authors_cn: list[str] = field(default_factory=list)
    authors_en: list[str] = field(default_factory=list)
    first_author_cn: str = ""
    first_author_en: str = ""
    journal_cn: str = ""
    journal_en: str = ""
    year: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    institution_cn: str = ""
    institution_en: str = ""
    country: str = ""
    doi: str = ""

    @property
    def authors_str(self) -> str:
        """返回所有作者的字符串表示"""
        authors = self.authors_cn if self.authors_cn else self.authors_en
        return ", ".join(authors)

    @property
    def first_author(self) -> str:
        return self.first_author_cn or self.first_author_en

    @property
    def other_authors(self) -> str:
        authors = self.authors_cn if self.authors_cn else self.authors_en
        if len(authors) > 1:
            return ", ".join(authors[1:])
        return ""


@dataclass
class ParseResult:
    """PDF 解析结果"""
    full_text: str
    text_blocks: list[TextBlock]
    metadata: PaperMetadata
    page_count: int


def extract_text_blocks(doc: fitz.Document) -> list[TextBlock]:
    """提取所有文本块及其位置信息"""
    blocks = []
    for page_num in range(doc.page_count):
        page = doc[page_num]
        # 使用 dict 模式提取，保留精确坐标
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:  # 只处理文本块
                continue
            block_text_parts = []
            for line in block.get("lines", []):
                line_text = ""
                for span in line.get("spans", []):
                    line_text += _clean_pdf_text(span.get("text", ""))
                block_text_parts.append(line_text.strip())
            text = " ".join(part for part in block_text_parts if part)
            if text.strip():
                bbox = (block["bbox"][0], block["bbox"][1],
                        block["bbox"][2], block["bbox"][3])
                blocks.append(TextBlock(
                    text=text.strip(),
                    page_num=page_num,
                    bbox=bbox,
                ))
    return blocks


def _clean_pdf_text(text: str) -> str:
    """清理 PDF 提取文本中的控制字符和编码残留

    PDF 字体编码有时将特殊字符（如捷克语变音符号）编码为 \x01-\x1f 控制字符，
    直接删除这些字符（不替换），避免作者名出现乱码。
    """
    # 删除 ASCII 控制字符（\x00-\x1f），保留 \t \n \r
    # 同时删除 Unicode 替换字符 \ufffd（PDF 字体私有编码无法解码时产生）
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]', '', text)


def _ocr_page(page: fitz.Page) -> str:
    """对单页进行 OCR，支持双栏布局（按 x 坐标分栏拼接）"""
    ocr = _get_ocr()
    if ocr is None:
        return ""
    try:
        import numpy as np
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        result, _ = ocr(img)
        if not result:
            return ""

        # 每条 result: (bbox [[x0,y0],[x1,y1],[x2,y2],[x3,y3]], text, score)
        # 判断是否双栏：若文本块 x 中心点分布在两个明显聚类，则按栏拼接
        page_width = pix.width
        mid_x = page_width / 2

        left_lines, right_lines = [], []
        for item in result:
            bbox, text, _ = item[0], item[1], item[2]
            x_center = (bbox[0][0] + bbox[2][0]) / 2
            y_top = bbox[0][1]
            if x_center < mid_x:
                left_lines.append((y_top, text))
            else:
                right_lines.append((y_top, text))

        # 若右栏为空，视为单栏
        if not right_lines:
            return "\n".join(t for _, t in sorted(left_lines))

        # 双栏：左栏在前，右栏在后，各自按 y 排序
        left_text = "\n".join(t for _, t in sorted(left_lines))
        right_text = "\n".join(t for _, t in sorted(right_lines))
        return left_text + "\n" + right_text

    except Exception as e:
        logger.warning(f"OCR 失败 page={page.number}: {e}")
    return ""


def _build_full_text(doc: fitz.Document) -> str:
    """提取全文文本，逐页拼接；文字层为空时自动 OCR 降级"""
    pages_text = []
    for page_num in range(doc.page_count):
        page = doc[page_num]
        text = _clean_pdf_text(page.get_text("text"))
        if not text.strip():
            text = _ocr_page(page)
            if text:
                logger.info(f"页面 {page_num+1} 文字层为空，已用 OCR 识别 ({len(text)} 字符)")
        pages_text.append(text)
    return "\n".join(pages_text)


def _extract_metadata(full_text: str, doc: fitz.Document) -> PaperMetadata:
    """从论文文本中提取施评文献的元数据

    自动检测中文/英文论文格式并调用对应的提取逻辑。
    """
    # 判断论文语言：首页中文字符超过 20% 则认为是中文论文
    first_page = _clean_pdf_text(doc[0].get_text("text")) if doc.page_count > 0 else ""
    # 扫描版 PDF 文字层为空，用 full_text 前段代替
    if not first_page.strip():
        first_page = full_text[:3000]
    cn_chars = sum(1 for c in first_page if '\u4e00' <= c <= '\u9fff')
    is_chinese = cn_chars > len(first_page) * 0.2

    if is_chinese:
        return _extract_metadata_cn(full_text, doc, first_page)
    else:
        return _extract_metadata_en(full_text, doc, first_page)


def _extract_metadata_cn(full_text: str, doc: fitz.Document, first_page: str) -> PaperMetadata:
    """从中文论文中提取元数据"""
    meta = PaperMetadata()

    # 提取 DOI
    doi_match = re.search(r'D[O0]I[：:\s]*(10\.\s*\d{4,}/[^\n]+)', full_text, re.IGNORECASE)
    if doi_match:
        meta.doi = re.sub(r'\s+', '', doi_match.group(1).strip())

    # 提取年份 - 从期刊信息行
    year_match = re.search(r'(\d{4})年\d{1,2}月', full_text)
    if year_match:
        meta.year = year_match.group(1)

    # 提取文章编号行后的中文标题
    title_match = re.search(r'文章编号：[^\n]+\n(.+?)(?:\*|\n[（(])', first_page, re.DOTALL)
    if title_match:
        title_lines = title_match.group(1).strip().split('\n')
        meta.title_cn = ''.join(line.strip() for line in title_lines)

    # 提取作者行 - 在标题和机构之间
    author_match = re.search(r'分析\*?\n(.+?)\n[（(]', first_page, re.DOTALL)
    if not author_match:
        author_match = re.search(
            r'(?:预测及时序分析|[\u4e00-\u9fff]{2,})\*?\n([^\n]*?(?:，|,)[^\n]*?)\n[（(]',
            first_page, re.DOTALL,
        )

    if author_match:
        author_line = author_match.group(1).strip()
        author_line = re.sub(r'[0-9０-９\u00b9\u00b2\u00b3\u2070-\u209f]', '', author_line)
        author_line = re.sub(r'["""\u201c\u201d\*]+', '', author_line)
        author_line = re.sub(r'，', ',', author_line)
        authors = [a.strip() for a in re.split(r'[,]', author_line) if a.strip()]
        meta.authors_cn = authors
        if authors:
            meta.first_author_cn = authors[0]

    # 提取机构
    inst_match = re.search(r'[（(]\s*\d?\s*(.+?)(?:，|,)\s*[\u4e00-\u9fff]+\d{6}', first_page)
    if inst_match:
        meta.institution_cn = inst_match.group(1).strip()

    # 从最后一页提取英文标题
    last_page = doc[-1].get_text("text") if doc.page_count > 0 else ""
    en_title_match = re.search(
        r'\n((?:Safety|Risk|Analysis|Prediction|Study|Research|Effect|Impact|'
        r'Application|Development|Design|Optimization|Evaluation|Assessment|'
        r'Investigation|Experimental|Numerical|A\s|The\s)[^\n]{10,120})\n',
        last_page,
    )
    if en_title_match:
        candidate = en_title_match.group(1).strip()
        if '[J]' not in candidate and '[C]' not in candidate and len(candidate) > 20:
            meta.title_en = candidate

    # 提取期刊名
    for page_idx in range(1, min(4, doc.page_count)):
        page = doc[page_idx]
        blocks = page.get_text('blocks')
        for b in blocks:
            if b[1] > 200 or b[-1] != 0:
                continue
            block_text = b[4].strip()
            cn_chars = re.sub(r'[^\u4e00-\u9fff]', '', block_text)
            cn_chars = re.sub(r'[年月日第卷期号]', '', cn_chars)
            cn_chars = cn_chars.strip()
            for jp in [
                r'([\u4e00-\u9fff]*与[\u4e00-\u9fff]*学报)',
                r'([\u4e00-\u9fff]{2,}学报)',
                r'([\u4e00-\u9fff]{2,}杂志)',
            ]:
                m = re.search(jp, cn_chars)
                if m and len(m.group(1)) >= 3:
                    meta.journal_cn = m.group(1)
                    break
            if meta.journal_cn:
                break
        if meta.journal_cn:
            break

    if not meta.journal_cn:
        en_match = re.search(
            r'Journal\s+of\s+\w[\w\s]+?(?=\d{4}|\n)',
            first_page, re.IGNORECASE,
        )
        if en_match:
            meta.journal_en = re.sub(r'\s+', ' ', en_match.group(0)).strip()

    # 提取卷期页码
    vol_match = re.search(r'(?:Vol\.|第)\s*(\d+)\s*(?:卷)', first_page)
    if vol_match:
        meta.volume = vol_match.group(1)
    issue_match = re.search(r'(?:No\.|第)\s*(\d+)\s*(?:期)', first_page)
    if issue_match:
        meta.issue = issue_match.group(1)
    pages_match = re.search(r'文章编号：\d{4}-\d{4}\(\d{4}\)\d{2}-(\d{4})-(\d+)', first_page)
    if pages_match:
        start_page = int(pages_match.group(1))
        page_count = int(pages_match.group(2))
        meta.pages = f"{start_page}-{start_page + page_count - 1}"

    # 国家 - 中文论文默认中国
    if meta.institution_cn:
        meta.country = "中国"

    return meta


def _extract_metadata_en(full_text: str, doc: fitz.Document, first_page: str) -> PaperMetadata:
    """从英文论文中提取元数据

    典型首页格式（Springer / Elsevier / ACS 等）：
    - 期刊名 (年份) 卷:页码
    - DOI 行
    - 文章类型（REVIEW ARTICLE / RESEARCH ARTICLE）
    - 论文标题（通常是最大字号的文本块）
    - 作者行（Name1 · Name2 · ...，带上标数字）
    - 机构信息（上标数字 + 机构名称）
    """
    meta = PaperMetadata()

    # ── 1. 提取 DOI（支持跨行，如 "10.1016/\nj.isci.2022.104642"）──
    # 先合并 DOI 跨行：'10.xxxx/xxx\n后缀' → '10.xxxx/xxx后缀'
    _doi_text = re.sub(r'(10\.\d{4,}/[^\n]*)\n([^\n]{3,})', lambda m: m.group(1) + m.group(2) if '.' in m.group(2) else m.group(0), full_text)
    doi_match = re.search(
        r'(?:doi\.org/|D[O0]I[：:\s]*)(10\.\d{4,}/\S+)',
        _doi_text, re.IGNORECASE,
    )
    if doi_match:
        meta.doi = re.sub(r'\s+', '', doi_match.group(1).strip().rstrip('.'))

    # ── 2. 提取期刊名、年份、卷期、页码 ──
    # 格式1: "Journal Name (2021) 4:1–34"
    journal_match = re.search(
        r'^(.+?)\s*\((\d{4})\)\s*(\d+)(?:\((\d+)\))?[:\s]*(\d+[\u2013\-–]\d+)',
        first_page, re.MULTILINE,
    )
    if journal_match:
        meta.journal_en = journal_match.group(1).strip()
        # 清理期刊名中的噪音（如换行符、多余空格）
        meta.journal_en = re.sub(r'\s+', ' ', meta.journal_en)
        # 去掉末尾可能的换行残留
        meta.journal_en = meta.journal_en.strip()
        meta.year = journal_match.group(2)
        meta.volume = journal_match.group(3)
        if journal_match.group(4):
            meta.issue = journal_match.group(4)
        meta.pages = journal_match.group(5).replace('\u2013', '-').replace('–', '-')
    else:
        # 格式2: "Journal Name, Volume X, Issue Y, Year, Pages"
        # 或从 header 区域提取
        _extract_journal_from_header(doc, meta)

    # 格式3（兜底）: "Journal Volume (Year) ArticleNumber"，如 "Heliyon 7 (2021) e06955"
    if not meta.journal_en:
        m = re.search(
            r'^([A-Z][A-Za-z\s]+?)\s+(\d+)\s*\((\d{4})\)\s*([a-zA-Z]\d+)',
            first_page, re.MULTILINE,
        )
        if m:
            meta.journal_en = m.group(1).strip()
            meta.volume = m.group(2)
            meta.year = meta.year or m.group(3)
            meta.pages = m.group(4)

    # ── 3. 提取标题（从 PDF 块结构中找最大字号的文本块）──
    _extract_title_from_blocks(doc, meta)

    # ── 4. 提取作者列表 ──
    _extract_authors_from_blocks(doc, meta)

    # ── 5. 提取机构信息 ──
    _extract_institution_from_blocks(doc, meta)

    # ── 6. 兜底：从纯文本正则提取 ──
    if not meta.year:
        year_match = re.search(r'\((\d{4})\)', first_page)
        if year_match:
            meta.year = year_match.group(1)

    return meta


def _extract_journal_from_header(doc: fitz.Document, meta: PaperMetadata):
    """从页眉/页脚区域提取英文期刊名

    支持两种格式：
    - 格式A（Springer 等）：页眉 "Journal Name (2021) 4:1-34"
    - 格式B（Cell 系列）：首页底部 "Author et al., 2021, Cell 184, 1362-1376"
    """
    # ── 策略1：首页底部 Cell 风格引用 ──
    # 格式: "Author et al., 2021, Cell 184, 1362–1376"
    # 通常在首页 y > 650 的区域
    if doc.page_count > 0:
        page0 = doc[0]
        blocks0 = page0.get_text('blocks')
        for b in blocks0:
            if b[-1] != 0:
                continue
            y_top = b[1]
            if y_top < 650:  # 仅检查页面底部区域
                continue
            block_text = _clean_pdf_text(b[4].strip())
            # Cell 格式: "Wang et al., 2021, Cell 184, 1362–1376"
            # 也匹配: "Author1 and Author2, 2021, Nature 595, 100-105"
            m = re.match(
                r'.+?,\s*(\d{4}),\s*'           # 年份
                r'([A-Z][A-Za-z\s&]+?)\s+'       # 期刊名（以大写开头）
                r'(\d+),\s*'                      # 卷号
                r'(\d+[\u2013\-–]\d+)',           # 页码范围
                block_text,
            )
            if m:
                if not meta.year:
                    meta.year = m.group(1)
                if not meta.journal_en:
                    meta.journal_en = m.group(2).strip()
                if not meta.volume:
                    meta.volume = m.group(3)
                if not meta.pages:
                    meta.pages = m.group(4).replace('\u2013', '-').replace('–', '-')
                return

            # Current Biology / Elsevier 格式（先检查，防止 m2 误匹配月份名）:
            # "Current Biology 31, R1252–R1266, October 11, 2021"
            m3 = re.search(
                r'([A-Z][A-Za-z\s]+?)\s+(\d+),\s*([A-Z]?\d+[\u2013\-–][A-Z]?\d+).*?(\d{4})',
                block_text,
            )
            if m3:
                if not meta.journal_en:
                    meta.journal_en = m3.group(1).strip()
                if not meta.volume:
                    meta.volume = m3.group(2)
                if not meta.pages:
                    meta.pages = m3.group(3).replace('\u2013', '-').replace('–', '-')
                if not meta.year:
                    meta.year = m3.group(4)
                return

            # iScience/Cell 变体: "He & Lamont, iScience 25,\n104642\nJuly 15, 2022"
            # 年份在后续行，页码是文章编号（纯数字）
            m2 = re.match(
                r'.+?,\s*'
                r'([A-Za-z][A-Za-z\s&]+?)\s+'    # 期刊名（支持 iScience/eLife 等小写开头）
                r'(\d+),\s*'                      # 卷号
                r'(\d+)',                          # 文章编号（纯数字）
                block_text,
                re.DOTALL,
            )
            if m2:
                year_m = re.search(r'(\d{4})', block_text[m2.end():])
                if year_m:
                    if not meta.year:
                        meta.year = year_m.group(1)
                    if not meta.journal_en:
                        meta.journal_en = m2.group(1).strip()
                    if not meta.volume:
                        meta.volume = m2.group(2)
                    if not meta.pages:
                        meta.pages = m2.group(3)
                    return

    # ── 策略2：第 2-4 页的页眉区域 ──
    if doc.page_count < 2:
        return

    for page_idx in range(1, min(4, doc.page_count)):
        page = doc[page_idx]
        blocks = page.get_text('blocks')
        for b in blocks:
            if b[1] > 80 or b[-1] != 0:  # 仅页面顶部 80pt
                continue
            block_text = b[4].strip()
            # 英文期刊名通常在页眉，后跟 (年) 卷:页码
            m = re.match(
                r'(.+?)\s*\((\d{4})\)\s*(\d+)[:\s]*(\d+[\u2013\-–]\d+)',
                block_text,
            )
            if m:
                if not meta.journal_en:
                    meta.journal_en = re.sub(r'\s+', ' ', m.group(1).strip())
                if not meta.year:
                    meta.year = m.group(2)
                if not meta.volume:
                    meta.volume = m.group(3)
                if not meta.pages:
                    meta.pages = m.group(4).replace('\u2013', '-').replace('–', '-')
                return


def _extract_title_from_blocks(doc: fitz.Document, meta: PaperMetadata):
    """从 PDF 首页块结构中提取标题（通常是最大字号的文本块）"""
    if doc.page_count == 0:
        return

    page = doc[0]
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    # 收集所有文本块及其最大字号
    candidates = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        max_font_size = 0
        block_text_parts = []
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font_size = span.get("size", 0)
                max_font_size = max(max_font_size, font_size)
                block_text_parts.append(_clean_pdf_text(span.get("text", "")))
        text = " ".join(block_text_parts).strip()
        # 合并行内换行
        text = re.sub(r'\s+', ' ', text)
        # 排除过短文本、纯数字、期刊名行、DOI行、类型标签
        if len(text) < 10:
            continue
        if re.match(r'^[\d\s.]+$', text):
            continue
        if 'doi.org' in text.lower() or 'DOI' in text:
            continue
        if text.strip() in ('REVIEW ARTICLE', 'RESEARCH ARTICLE', 'ORIGINAL ARTICLE',
                           'LETTER', 'COMMUNICATION', 'SHORT COMMUNICATION'):
            continue
        # y 坐标：标题通常在页面中上部（y < 400）
        y_top = block["bbox"][1]
        if y_top > 400:
            continue

        candidates.append((max_font_size, text, y_top))

    if not candidates:
        return

    # 按字号降序排列，取最大字号的块作为标题
    candidates.sort(key=lambda x: (-x[0], x[2]))
    title_text = candidates[0][1]
    # 去掉开头的文章类型标签（如 "Article ", "Review Article "）
    title_text = re.sub(
        r'^(?:Article|Review(?:\s+Article)?|Research\s+Article|Original\s+Article|'
        r'Letter|Communication|Short\s+Communication)\s+',
        '', title_text, flags=re.IGNORECASE,
    )
    # 清理 non-breaking space 和特殊连字符
    title_text = title_text.replace('\xa0', ' ')
    title_text = title_text.replace('\u2011', '-')  # non-breaking hyphen
    title_text = title_text.replace('\u2010', '-')  # hyphen
    meta.title_en = title_text.strip()


def _extract_authors_from_blocks(doc: fitz.Document, meta: PaperMetadata):
    """从 PDF 首页块结构中提取作者列表

    英文论文作者行通常用中间点 (·, ·) 或逗号分隔，带上标数字。
    支持多种布局：
    - 标题正下方的作者行（Springer / Elsevier Review 等）
    - 右栏 "Authors" 标签下方的作者列表（Cell Graphical Abstract 布局）
    - 逗号分隔的作者列表
    - 第2页完整作者列表（Cell 等期刊首页 Graphical Abstract 截断时）
    """
    if doc.page_count == 0:
        return

    page = doc[0]
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    def _get_span_text(span: dict) -> str:
        return _clean_pdf_text(span.get("text", ""))

    # 找标题块的 y 坐标下界
    # 要求匹配前缀至少 20 字符，避免短词（如 "Rhamnaceae"）误匹配图表文字
    title_y_bottom = 0
    if meta.title_en:
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            block_text = ""
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    block_text += _get_span_text(span)
            simplified = re.sub(r'\s+', ' ', block_text).strip()
            title_simplified = re.sub(r'\s+', ' ', meta.title_en).strip()
            prefix_len = min(30, len(title_simplified), len(simplified))
            if prefix_len >= 20 and (
                title_simplified[:prefix_len] in simplified
                or simplified[:prefix_len] in title_simplified
            ):
                title_y_bottom = block["bbox"][3]
                break

    # ── 策略1：搜索 "Authors" 标签，取其下方同 x 范围的块作为作者列表 ──
    all_blocks = page_dict.get("blocks", [])
    found_truncated = False  # 标记是否找到了截断的作者列表（含 ...）
    for idx, block in enumerate(all_blocks):
        if block.get("type") != 0:
            continue
        block_text = ""
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                block_text += _clean_pdf_text(span.get("text", ""))
        if block_text.strip() == "Authors":
            authors_x0 = block["bbox"][0]
            authors_y = block["bbox"][3]
            for next_block in all_blocks[idx + 1:]:
                if next_block.get("type") != 0:
                    continue
                nb_x0 = next_block["bbox"][0]
                nb_y0 = next_block["bbox"][1]
                if abs(nb_x0 - authors_x0) < 50 and 0 < nb_y0 - authors_y < 30:
                    nb_text = ""
                    for line in next_block.get("lines", []):
                        for span in line.get("spans", []):
                            nb_text += _clean_pdf_text(span.get("text", ""))
                    # 检查是否截断（含省略号）
                    if '...' in nb_text or '…' in nb_text:
                        found_truncated = True
                        break
                    authors = _parse_author_text(nb_text)
                    if authors:
                        meta.authors_en = authors
                        meta.first_author_en = authors[0]
                        return
                    break

    # ── 策略1b：首页作者截断时，从第2页提取完整作者列表 ──
    # Cell 等期刊首页是 Graphical Abstract，完整作者列表在第2页
    # 第2页的块可能将作者+机构合并，需截断到机构编号行之前
    if found_truncated and doc.page_count > 1:
        page2_dict = doc[1].get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        for block in page2_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            block_text = ""
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    block_text += _clean_pdf_text(span.get("text", ""))
            if not (',' in block_text and re.search(r'[A-Z][a-z]+ [A-Z][a-z]+', block_text)):
                continue
            first_word = block_text.strip().split()[0] if block_text.strip() else ''
            if first_word.lower() in ('summary', 'introduction', 'abstract', 'methods',
                                      'results', 'discussion', 'background'):
                continue
            # 截断到机构编号行之前（"\n1School of..." 或 "\n1 Department of..."）
            author_part = re.split(r'\n\d+[A-Z]|\n\d+\s+[A-Z]', block_text)[0]
            # 用专用函数解析 "Name,数字,数字 Name2,数字" 格式
            authors = _parse_author_text_with_superscripts(author_part)
            if len(authors) >= 3:
                meta.authors_en = authors
                meta.first_author_en = authors[0]
                return

    # ── 策略2：在标题下方遍历所有 y < 350 的块，找作者行 ──
    for block in all_blocks:
        if block.get("type") != 0:
            continue
        y_top = block["bbox"][1]

        if title_y_bottom and y_top < title_y_bottom:
            continue
        if y_top > 350:
            continue

        line_texts = []
        for line in block.get("lines", []):
            line_text = "".join(_clean_pdf_text(span.get("text", "")) for span in line.get("spans", []))
            line_texts.append(line_text)
        block_text = " ".join(line_texts)

        stripped = block_text.strip()
        if stripped in ("Authors", "Graphical abstract", "Highlights",
                       "In Brief", "Correspondence", "Summary"):
            continue
        if stripped.startswith("d "):  # Cell highlights 格式
            continue

        if '·' in block_text or '\u00b7' in block_text:
            authors = _parse_author_text_dot(block_text)
            if authors:
                meta.authors_en = authors
                meta.first_author_en = authors[0]
                return

        if ',' in block_text and re.search(r'[A-Z][a-z]+\s+[A-Z]', block_text):
            # 截断到机构行之前（作者名与机构可能在同一块）
            author_lines = []
            for line in block.get("lines", []):
                line_text = "".join(_clean_pdf_text(s.get("text", "")) for s in line.get("spans", []))
                if re.match(r'(?:Department|Institute|University|School|Center|College|Lab|Faculty|Correspondence)', line_text.strip()):
                    break
                author_lines.append(line_text)
            author_block_text = " ".join(author_lines) if author_lines else block_text
            authors = _parse_author_text(author_block_text)
            if len(authors) >= 2:
                meta.authors_en = authors
                meta.first_author_en = authors[0]
                return

        # 单作者：无逗号/点，格式 "FirstName [MiddleName] LastName [*]"
        stripped_name = re.sub(r'[\s*]+$', '', block_text).strip()
        words = stripped_name.split()
        if (2 <= len(words) <= 4
                and all(re.match(r'^[A-Z][a-z]+$', w) for w in words)
                and len(stripped_name) < 60):
            meta.authors_en = [stripped_name]
            meta.first_author_en = stripped_name
            return


def _parse_author_text_dot(text: str) -> list[str]:
    """解析以中间点 · 分隔的作者文本"""
    # 清理：移除上标数字、\xa0、控制字符
    author_line = text
    author_line = re.sub(r'[\u00b9\u00b2\u00b3\u2070-\u2079\u2080-\u2089]', '', author_line)
    author_line = re.sub(r'(?<=[a-zA-Z])\d{1,2}(?:,\d{1,2})*', '', author_line)
    author_line = author_line.replace('\xa0', ' ')
    # 以中间点分割
    parts = re.split(r'\s*[·\u00b7]\s*', author_line)
    authors = []
    for part in parts:
        name = part.strip()
        name = re.sub(r'\s*\ue001\s*', '', name)
        if name and len(name) > 1 and not re.match(r'^\d+$', name):
            authors.append(name)
    return authors


def _parse_author_text(text: str) -> list[str]:
    """解析以逗号分隔的作者文本（支持 ... 省略号）"""
    author_line = re.sub(r'[\u00b9\u00b2\u00b3\u2070-\u2079]', '', text)
    author_line = re.sub(r'(?<=[a-zA-Z])\d{1,2}', '', author_line)
    author_line = author_line.replace('\xa0', ' ')
    # 将 " and " 转为逗号分隔（避免 "A and B" 被合并成一个token）
    author_line = re.sub(r',?\s+and\s+', ', ', author_line)
    # 移除省略号
    author_line = author_line.replace('...', ',').replace('…', ',')
    parts = re.split(r',\s*', author_line)
    authors = []
    for part in parts:
        name = part.strip().strip('.')
        if name and len(name) > 2 and re.match(r'[A-Z]', name):
            # 排除非名字的词
            if name.lower() in ('authors', 'correspondence', 'highlights',
                               'graphical abstract', 'in brief'):
                continue
            authors.append(name)
    return authors


def _parse_author_text_with_superscripts(text: str) -> list[str]:
    """解析 'Name,数字,数字 Name2,数字' 格式的作者文本（Cell 等期刊）

    格式特征：每个作者名后跟逗号+上标数字，作者间用空格分隔。
    例：'Kun Wang,1,17 Jun Wang,2,3,17 Chenglong Zhu,1,4,17'
    """
    # 先移除 "and " 前缀
    text = re.sub(r'\band\s+', '', text)
    # 移除 *（通讯作者标记）
    text = re.sub(r',\*|\*', '', text)
    # 用正则直接提取 "名字,数字" 模式中的名字部分
    # 匹配：大写字母开头的名字（可含空格），后跟逗号+数字
    authors = re.findall(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)(?:,\d+)+', text)
    # 去重并保持顺序
    seen = set()
    result = []
    for a in authors:
        a = a.strip()
        if a and a not in seen:
            seen.add(a)
            result.append(a)
    return result


def _extract_institution_from_blocks(doc: fitz.Document, meta: PaperMetadata):
    """从 PDF 前两页提取第一作者机构信息

    英文论文的机构信息通常在首页底部或页脚，以上标数字编号标识。
    Cell 等期刊的机构信息在第二页（首页为 Graphical Abstract 布局）。
    第一作者的机构编号通常为 1。
    """
    if doc.page_count == 0:
        return

    section_words = {
        'introduction', 'methods', 'results', 'discussion', 'conclusions',
        'background', 'methodology', 'experiment', 'experimental',
        'materials', 'overview', 'theory', 'model', 'analysis',
    }

    # 搜索前两页（Cell 等期刊机构信息在第2页）
    max_pages = min(2, doc.page_count)
    for page_idx in range(max_pages):
        page = doc[page_idx]
        blocks = page.get_text('blocks')

        # 第1页仅搜索下半部分（y > 400），第2页搜索全页
        y_min = 400 if page_idx == 0 else 0

        for b in blocks:
            if b[-1] != 0:  # 仅文本块
                continue
            y_top = b[1]
            text = b[4].strip()

            if y_top < y_min:
                continue

            # 匹配以数字标号开头的机构信息：
            # "1\tInstitute of Advanced Materials..."
            # "1 Department of Chemistry..."
            inst_match = re.match(
                r'^1[\t\s]+(.+)',
                text, re.DOTALL,
            )
            if inst_match:
                institution = inst_match.group(1).strip()

                # 排除章节标题（如 "1 Introduction"、"1 Methods"）
                first_word = institution.split()[0].lower() if institution.split() else ''
                if first_word in section_words:
                    continue
                # 合并多行
                institution = re.sub(r'\s*\n\s*', ' ', institution)
                # 清理特殊空格
                institution = institution.replace('\xa0', ' ')
                # 去除末尾邮箱
                institution = re.sub(r'\s*[\w.-]+@[\w.-]+\s*$', '', institution)
                # 去除过长的部分（可能包含了其他块的文本）
                if len(institution) > 300:
                    institution = institution[:300]

                meta.institution_en = institution

                # 推断国家
                meta.country = _infer_country_from_institution(institution)
                return

    # ── 策略2：从大文本块中用正则提取编号1的机构 ──
    # Cell 等期刊把作者和机构放在同一个大文本块中：
    # "Kun Wang,1,17 ... \n1School of Ecology..., China\n2College of..."
    for page_idx in range(max_pages):
        page_text = doc[page_idx].get_text("text")

        # 匹配 "\n1" + 机构名（紧邻或有空格），到 "\n2" 或 "\n17" 等下一个编号
        # 支持 "1School of..." 和 "1 School of..." 两种格式
        inst_match = re.search(
            r'\n1[\s]*([A-Z][A-Za-z].+?)(?:\n\d+[A-Z]|\n\d+\s+[A-Z]|\nReceived|\n\*|\nhttps?://)',
            page_text, re.DOTALL,
        )
        if inst_match:
            institution = inst_match.group(1).strip()
            institution = re.sub(r'\s*\n\s*', ' ', institution)
            institution = institution.replace('\xa0', ' ')

            # 排除章节标题
            first_word = institution.split()[0].lower() if institution.split() else ''
            if first_word in section_words:
                continue

            # 去除末尾邮箱
            institution = re.sub(r'\s*[\w.-]+@[\w.-]+\s*$', '', institution)
            if len(institution) > 300:
                institution = institution[:300]

            meta.institution_en = institution
            meta.country = _infer_country_from_institution(institution)
            return

    # ── 策略3：无编号前缀，扫描 y<350 的小型块，关键词可在任意位置 ──
    INST_RE = re.compile(
        r'(?:Department|Institute|University|School|Center|College|Laboratory|Faculty)',
        re.IGNORECASE,
    )
    page0 = doc[0]
    for b in page0.get_text('blocks'):
        if b[-1] != 0 or b[1] > 350:
            continue
        text = b[4].strip()
        if len(text) > 300 or not INST_RE.search(text):
            continue
        # 遍历行，找第一行含机构关键词的行（块内可能是作者名+机构混合）
        for line in (l.strip() for l in text.split('\n') if l.strip()):
            if INST_RE.search(line):
                institution = re.sub(r'\s*[\w.-]+@[\w.-]+\s*$', '', line).strip()
                if institution and len(institution) > 10:
                    meta.institution_en = institution
                    meta.country = _infer_country_from_institution(institution)
                    return


def _infer_country_from_institution(institution: str) -> str:
    """从机构名推断国家（中文名）"""
    inst_lower = institution.lower()

    country_patterns = [
        # 直接包含国名
        (r'\bchina\b', '中国'),
        (r'\busa\b|\bunited states\b|\bu\.s\.a\b', '美国'),
        (r'\bunited kingdom\b|\bengland\b|\bscotland\b|\bwales\b|\buk\b', '英国'),
        (r'\bjapan\b', '日本'),
        (r'\bgermany\b', '德国'),
        (r'\bfrance\b', '法国'),
        (r'\bcanada\b', '加拿大'),
        (r'\baustralia\b', '澳大利亚'),
        (r'\bkorea\b', '韩国'),
        (r'\bindia\b', '印度'),
        (r'\brussia\b', '俄罗斯'),
        (r'\bitalya?\b|\bitaly\b', '意大利'),
        (r'\bspain\b', '西班牙'),
        (r'\bnetherlands\b|\bholland\b', '荷兰'),
        (r'\bsweden\b', '瑞典'),
        (r'\bswitzerland\b', '瑞士'),
        (r'\bbrazil\b', '巴西'),
        (r'\bsingapore\b', '新加坡'),
        (r'\bisrael\b', '以色列'),
        (r'\bbelgium\b', '比利时'),
        (r'\baustria\b', '奥地利'),
        (r'\bdenmark\b', '丹麦'),
        (r'\bnorway\b', '挪威'),
        (r'\bfinland\b', '芬兰'),
        (r'\bpoland\b', '波兰'),
        (r'\bportugal\b', '葡萄牙'),
        (r'\bturkey\b|\btürkiye\b', '土耳其'),
        (r'\bmexico\b', '墨西哥'),
        (r'\btaiwan\b', '中国'),
        (r'\bhong kong\b', '中国'),
        (r'\bsouth africa\b', '南非'),
        (r'\bnew zealand\b', '新西兰'),
        (r'\bczech\b', '捷克'),
        (r'\bgreece\b', '希腊'),
        (r'\bthailand\b', '泰国'),
        (r'\bmalaysia\b', '马来西亚'),
        (r'\biran\b', '伊朗'),
        (r'\bsaudi arabia\b', '沙特阿拉伯'),
        (r'\begypt\b', '埃及'),
        (r'\bpakistan\b', '巴基斯坦'),
        (r'\bbangladesh\b', '孟加拉国'),
        (r'\bvietnam\b', '越南'),
        (r'\bindonesia\b', '印度尼西亚'),
        (r'\bphilippines\b', '菲律宾'),
    ]

    for pattern, country in country_patterns:
        if re.search(pattern, inst_lower):
            return country

    # 通过城市/地区推断
    city_country = {
        'beijing': '中国', 'shanghai': '中国', 'nanjing': '中国',
        'guangzhou': '中国', 'shenzhen': '中国', 'hangzhou': '中国',
        'wuhan': '中国', 'chengdu': '中国', 'xi\'an': '中国',
        'tokyo': '日本', 'osaka': '日本', 'kyoto': '日本',
        'seoul': '韩国', 'busan': '韩国',
        'berlin': '德国', 'munich': '德国', 'hamburg': '德国',
        'london': '英国', 'oxford': '英国', 'cambridge': '英国',
        'paris': '法国', 'lyon': '法国',
        'moscow': '俄罗斯',
        'toronto': '加拿大', 'montreal': '加拿大', 'vancouver': '加拿大',
        'sydney': '澳大利亚', 'melbourne': '澳大利亚',
    }

    for city, country in city_country.items():
        if city in inst_lower:
            return country

    # 中国特有的 "省" 或 "市" 或邮政编码
    if re.search(r'\d{6}', institution):
        if re.search(r'jiangsu|zhejiang|guangdong|shandong|hubei|sichuan|henan', inst_lower):
            return '中国'

    return ""


def parse_pdf(pdf_path: str) -> ParseResult:
    """解析 PDF 文件，返回全文文本、文本块和元数据

    Args:
        pdf_path: PDF 文件路径

    Returns:
        ParseResult 包含全文文本、文本块列表和施评文献元数据
    """
    doc = fitz.open(pdf_path)
    try:
        full_text = _build_full_text(doc)
        text_blocks = extract_text_blocks(doc)
        metadata = _extract_metadata(full_text, doc)
        return ParseResult(
            full_text=full_text,
            text_blocks=text_blocks,
            metadata=metadata,
            page_count=doc.page_count,
        )
    finally:
        doc.close()
