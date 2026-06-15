"""MinerU PDF 解析模块

用 magic-pdf CLI 替代 PyMuPDF，输出 Markdown 文本，
接口与 pdf_parser.py 完全相同（ParseResult / PaperMetadata / TextBlock）。
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.pdf_parser import ParseResult, PaperMetadata, TextBlock, parse_pdf as _pymupdf_parse

logger = logging.getLogger(__name__)


def parse_pdf(pdf_path: str, output_dir: str | None = None) -> ParseResult:
    """用 MinerU 解析 PDF，返回 ParseResult（full_text 为 Markdown 内容）

    Args:
        pdf_path: PDF 文件路径
        output_dir: 若指定，将生成的 .md 文件复制到该目录

    Returns:
        ParseResult，其中：
        - full_text: MinerU 生成的 Markdown 文本
        - text_blocks: 空列表（MinerU 不提供坐标，高亮仍用原始 PDF）
        - metadata: 从 Markdown 正则提取的元数据
        - page_count: 0（MinerU CLI 不直接返回页数）
    """
    pdf_path = str(pdf_path)
    pdf_stem = Path(pdf_path).stem

    with tempfile.TemporaryDirectory() as tmp_dir:
        # 查找 magic-pdf 可执行文件：优先当前 Python 解释器同目录
        magic_pdf_bin = shutil.which("magic-pdf")
        if not magic_pdf_bin:
            import sys
            candidate = os.path.join(os.path.dirname(sys.executable), "magic-pdf")
            if os.path.isfile(candidate):
                magic_pdf_bin = candidate
        # 搜索项目本地 venv（app 可能用系统 Python 启动，但 magic-pdf 装在 venv 里）
        if not magic_pdf_bin:
            project_root = Path(__file__).parent.parent
            for venv_dir in ("venv312", "venv", ".venv"):
                candidate = project_root / venv_dir / "bin" / "magic-pdf"
                if candidate.is_file():
                    magic_pdf_bin = str(candidate)
                    break
        if not magic_pdf_bin:
            raise RuntimeError(
                "magic-pdf 未安装，请运行: pip install 'magic-pdf[full]' && magic-pdf --download-models"
            )

        cmd = [magic_pdf_bin, "-p", pdf_path, "-o", tmp_dir, "-m", "auto"]
        logger.info(f"MinerU 解析: {' '.join(cmd)}")

        # 设置环境变量：HF 镜像（解决 HuggingFace 访问问题）
        env = os.environ.copy()
        env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if result.returncode != 0:
                logger.error(f"MinerU 失败 (code={result.returncode}): {result.stderr[:500]}")
                raise RuntimeError(f"magic-pdf 失败: {result.stderr[:200]}")
            if result.stderr:
                logger.debug(f"MinerU stderr: {result.stderr[:300]}")
        except FileNotFoundError:
            raise RuntimeError(
                "magic-pdf 未安装，请运行: pip install 'magic-pdf[full]' && magic-pdf --download-models"
            )

        # 查找输出的 .md 文件
        # MinerU 输出目录结构: {tmp_dir}/{pdf_stem}/auto/{pdf_stem}.md
        md_files = list(Path(tmp_dir).rglob("*.md"))
        if not md_files:
            raise RuntimeError(f"MinerU 未生成 Markdown 文件，输出目录: {tmp_dir}")

        # 优先取与 PDF 同名的 md，否则取第一个
        md_file = next((f for f in md_files if f.stem == pdf_stem), md_files[0])
        md_text = md_file.read_text(encoding="utf-8")
        logger.info(f"MinerU 解析完成，Markdown {len(md_text)} 字符")

        # 复制 md 到输出目录
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            dest = os.path.join(output_dir, f"{pdf_stem}.md")
            shutil.copy2(md_file, dest)
            logger.info(f"Markdown 已保存: {dest}")

        # 元数据用 PyMuPDF 提取（更准确），MinerU 只提供全文 Markdown
        try:
            metadata = _pymupdf_parse(pdf_path).metadata
        except Exception as e:
            logger.warning(f"PyMuPDF 元数据提取失败，降级到正则: {e}")
            metadata = _extract_metadata(md_text)

        return ParseResult(
            full_text=md_text,
            text_blocks=[],
            metadata=metadata,
            page_count=0,
        )


def _extract_metadata(md_text: str) -> PaperMetadata:
    """从 Markdown 文本中提取施评文献元数据（正则，尽力提取）"""
    meta = PaperMetadata()

    # 判断语言
    cn_chars = sum(1 for c in md_text[:3000] if '\u4e00' <= c <= '\u9fff')
    is_chinese = cn_chars > len(md_text[:3000]) * 0.1

    # DOI（支持 DOI: 前缀和 https://doi.org/ 格式）
    doi_match = re.search(r'(?:D[O0]I[：:\s]*|https?://doi\.org/)(10\.\s*\d{4,}/\S+)', md_text, re.IGNORECASE)
    if doi_match:
        meta.doi = re.sub(r'\s+', '', doi_match.group(1).rstrip('.,)\\'))

    # 年份
    year_match = re.search(r'(?:^|\s)(\d{4})(?:\s*年|\s*[,，]\s*(?:Vol|vol|No|pp))', md_text[:2000])
    if not year_match:
        year_match = re.search(r'\((\d{4})\)', md_text[:2000])
    if year_match:
        meta.year = year_match.group(1)

    # 标题：取 Markdown 中最大标题（# 开头）
    title_match = re.search(r'^#\s+(.+)$', md_text, re.MULTILINE)
    if title_match:
        title = title_match.group(1).strip()
        if is_chinese:
            meta.title_cn = title
        else:
            meta.title_en = title

    if is_chinese:
        _extract_cn_metadata(md_text, meta)
    else:
        _extract_en_metadata(md_text, meta)

    return meta


def _extract_cn_metadata(text: str, meta: PaperMetadata) -> None:
    """中文论文元数据提取"""
    # 作者行：中文姓名列表（逗号/，分隔），在标题后
    author_match = re.search(
        r'(?:^|\n)((?:[\u4e00-\u9fff]{2,4}[，,]){1,}[\u4e00-\u9fff]{2,4})\s*\n',
        text,
    )
    if author_match:
        authors = re.split(r'[，,]', author_match.group(1).strip())
        authors = [a.strip() for a in authors if a.strip()]
        if authors:
            meta.authors_cn = authors
            meta.first_author_cn = authors[0]

    # 期刊名
    journal_match = re.search(r'([\u4e00-\u9fff]{2,}学报|[\u4e00-\u9fff]{2,}杂志)', text[:3000])
    if journal_match:
        meta.journal_cn = journal_match.group(1)

    # 机构
    inst_match = re.search(r'[（(]([^）)]*大学[^）)]*)[）)]', text[:3000])
    if not inst_match:
        inst_match = re.search(r'[（(]([^）)]*研究院[^）)]*)[）)]', text[:3000])
    if inst_match:
        meta.institution_cn = inst_match.group(1).strip()
        meta.country = "中国"

    # 卷期
    vol_match = re.search(r'第\s*(\d+)\s*卷', text[:2000])
    if vol_match:
        meta.volume = vol_match.group(1)
    issue_match = re.search(r'第\s*(\d+)\s*期', text[:2000])
    if issue_match:
        meta.issue = issue_match.group(1)


def _extract_en_metadata(text: str, meta: PaperMetadata) -> None:
    """英文论文元数据提取"""
    # 期刊名 + 年份 + 卷 + 页码（常见格式）
    journal_match = re.search(
        r'^(.{5,60}?)\s*\((\d{4})\)\s*(\d+)[:\s]*(\d+[\-–]\d+)',
        text[:3000], re.MULTILINE,
    )
    if journal_match:
        meta.journal_en = journal_match.group(1).strip()
        meta.year = meta.year or journal_match.group(2)
        meta.volume = journal_match.group(3)
        meta.pages = journal_match.group(4).replace('–', '-')

    # 作者行：先尝试 · 分隔，再尝试逗号分隔（MinerU 常见格式：Name1,2,\*, Name2,...）
    dot_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+(?:\s*[·•]\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)+)', text[:3000])
    if dot_match:
        parts = re.split(r'\s*[·•]\s*', dot_match.group(1))
        authors = [re.sub(r'\d', '', p).strip() for p in parts if p.strip()]
        authors = [a for a in authors if len(a) > 2]
        if authors:
            meta.authors_en = authors
            meta.first_author_en = authors[0]
    else:
        # 逗号分隔格式：标题后第一段，形如 "Name1,2,\*, Name2,..."
        # 找到标题后第一个非空行
        author_line_match = re.search(r'^#[^\n]+\n\n([^\n#]+)\n', text[:2000], re.MULTILINE)
        if author_line_match:
            raw = author_line_match.group(1)
            parts = re.split(r',\s*', raw)
            authors = []
            for p in parts:
                name = re.sub(r'[\d\*\\\^]', '', p).strip().rstrip(',')
                # 过滤掉 "and Name" 前缀
                name = re.sub(r'^and\s+', '', name).strip()
                # 至少含空格（名+姓）或连字符姓名
                if re.match(r'^[A-Z][A-Za-z\-\.]+(?:\s+[A-Za-z\-\.]+)+$', name):
                    authors.append(name)
            if len(authors) >= 2:
                meta.authors_en = authors
                meta.first_author_en = authors[0]
