"""PDF 文本提取模块

职责：
- 从 PDF 中提取全文文本（处理双栏排版）
- 保留每段文字的页码和坐标位置（用于后续高亮）
- 提取施评文献的元数据（作者、期刊、机构等）
"""

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF


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
                    line_text += span.get("text", "")
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


def _build_full_text(doc: fitz.Document) -> str:
    """提取全文文本，逐页拼接"""
    pages_text = []
    for page_num in range(doc.page_count):
        page = doc[page_num]
        text = page.get_text("text")
        pages_text.append(text)
    return "\n".join(pages_text)


def _extract_metadata(full_text: str, doc: fitz.Document) -> PaperMetadata:
    """从论文文本中提取施评文献的元数据"""
    meta = PaperMetadata()

    # 提取 DOI（PDF 中可能是 D0I 或 DOI，值中可能有空格）
    doi_match = re.search(r'D[O0]I:\s*(10\.\s*\d{4,}/[^\n]+)', full_text, re.IGNORECASE)
    if doi_match:
        meta.doi = re.sub(r'\s+', '', doi_match.group(1).strip())

    # 提取年份 - 从期刊信息行
    year_match = re.search(r'(\d{4})年\d{1,2}月', full_text)
    if year_match:
        meta.year = year_match.group(1)

    # 尝试从第一页提取标题和作者
    first_page = doc[0].get_text("text") if doc.page_count > 0 else ""

    # 提取文章编号行后的中文标题
    title_match = re.search(r'文章编号：[^\n]+\n(.+?)(?:\*|\n[（(])', first_page, re.DOTALL)
    if title_match:
        title_lines = title_match.group(1).strip().split('\n')
        meta.title_cn = ''.join(line.strip() for line in title_lines)

    # 提取作者行 - 在标题和机构之间
    # 中文作者通常在标题后、括号（机构）前
    author_match = re.search(r'分析\*?\n(.+?)\n[（(]', first_page, re.DOTALL)
    if not author_match:
        # 更通用的匹配：标题后到机构前
        author_match = re.search(r'(?:预测及时序分析|[\u4e00-\u9fff]{2,})\*?\n([^\n]*?(?:，|,)[^\n]*?)\n[（(]', first_page, re.DOTALL)

    if author_match:
        author_line = author_match.group(1).strip()
        # 清理上标数字和特殊字符
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
    # 英文摘要通常在最后一页，标题在 "Abstract:" 之前
    last_page = doc[-1].get_text("text") if doc.page_count > 0 else ""
    # 查找英文标题行（通常在英文作者列表之前的独立行）
    en_title_match = re.search(
        r'\n((?:Safety|Risk|Analysis|Prediction|Study|Research|Effect|Impact|'
        r'Application|Development|Design|Optimization|Evaluation|Assessment|'
        r'Investigation|Experimental|Numerical|A\s|The\s)[^\n]{10,120})\n',
        last_page,
    )
    if en_title_match:
        candidate = en_title_match.group(1).strip()
        # 排除参考文献中的标题（通常很短或包含 [J] 等标记）
        if '[J]' not in candidate and '[C]' not in candidate and len(candidate) > 20:
            meta.title_en = candidate

    # 提取期刊名
    meta.journal_cn = ""

    # 策略：从第2页（或第3页）header 区域提取
    # 中文期刊 PDF 的偶数页 header 通常包含期刊中文名
    for page_idx in range(1, min(4, doc.page_count)):
        page = doc[page_idx]
        blocks = page.get_text('blocks')
        for b in blocks:
            # header 区域通常在页面顶部 (y < 200)
            if b[1] > 200 or b[-1] != 0:  # type 0 = text
                continue
            block_text = b[4].strip()
            # 从 header block 提取纯中文字符
            cn_chars = re.sub(r'[^\u4e00-\u9fff]', '', block_text)
            # 删除卷期号等干扰字符
            cn_chars = re.sub(r'[年月日第卷期号]', '', cn_chars)
            cn_chars = cn_chars.strip()
            # 检查是否包含期刊名模式
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

    # 兜底：从英文期刊名提取
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
