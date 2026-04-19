"""Word 登记表写入模块

职责：
- 按模板格式生成每条评论句的 Word 登记表
- 评论句中标志词、作者姓氏、年份自动加粗
"""

import logging
import re

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from core.llm_analyzer import CommentRecord
from core.pdf_parser import PaperMetadata

logger = logging.getLogger(__name__)


def _set_cell_font(cell, font_name="Times New Roman", font_size=10.5):
    """设置单元格默认字体"""
    for paragraph in cell.paragraphs:
        for run in paragraph.runs:
            run.font.name = font_name
            run.font.size = Pt(font_size)
            run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)


def _write_bold_sentence(cell, sentence: str, marker: str, author: str, year: str):
    """在单元格中写入评论句，标志词/作者/年份加粗

    使用代码自动匹配方式定位需要加粗的部分。
    """
    cell.text = ""  # 清空
    paragraph = cell.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT

    # 收集所有需要加粗的位置
    bold_ranges = []

    # 查找标志词位置
    if marker:
        for m in re.finditer(re.escape(marker), sentence):
            bold_ranges.append((m.start(), m.end()))

    # 查找作者位置
    if author:
        for m in re.finditer(re.escape(author), sentence):
            bold_ranges.append((m.start(), m.end()))

    # 查找年份位置
    if year and len(year) == 4:
        for m in re.finditer(re.escape(year), sentence):
            bold_ranges.append((m.start(), m.end()))

    # 合并重叠区间
    bold_ranges.sort()
    merged = []
    for start, end in bold_ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # 按区间切分文本，分别添加 run
    pos = 0
    for start, end in merged:
        # 非加粗部分
        if pos < start:
            run = paragraph.add_run(sentence[pos:start])
            run.font.name = "Times New Roman"
            run.font.size = Pt(10.5)
            run._element.rPr.rFonts.set(qn('w:eastAsia'), "Times New Roman")
        # 加粗部分
        run = paragraph.add_run(sentence[start:end])
        run.bold = True
        run.font.name = "Times New Roman"
        run.font.size = Pt(10.5)
        run._element.rPr.rFonts.set(qn('w:eastAsia'), "Times New Roman")
        pos = end

    # 剩余部分
    if pos < len(sentence):
        run = paragraph.add_run(sentence[pos:])
        run.font.name = "Times New Roman"
        run.font.size = Pt(10.5)
        run._element.rPr.rFonts.set(qn('w:eastAsia'), "Times New Roman")


def _write_cell(cell, text: str, font_name="Times New Roman", font_size=10.5, bold=False):
    """写入单元格文本"""
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(str(text))
    run.font.name = font_name
    run.font.size = Pt(font_size)
    run.bold = bold
    run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)


def _format_ref_for_word(record: CommentRecord) -> str:
    """格式化被评文献引用（Word 登记表格式）"""
    ep = record.被评文献
    authors = ", ".join(ep.全部作者列表) if ep.全部作者列表 else ep.第一作者
    parts = [authors]
    if ep.文章名:
        parts[0] += f". {ep.文章名}[J]."
    if ep.期刊名称:
        parts.append(f" {ep.期刊名称},")
    if ep.年份:
        parts.append(f" {ep.年份},")
    vol = ""
    if ep.卷:
        vol = ep.卷
        if ep.期:
            vol += f"({ep.期})"
        vol += ":"
    if vol:
        parts.append(f" {vol}")
    if ep.起止页码:
        parts.append(ep.起止页码)
    return "".join(parts) + "."


def write_word(
    output_path: str,
    record: CommentRecord,
    metadata: PaperMetadata,
    index: int = 1,
    institution_info: dict | None = None,
    provider: str = "",
) -> str:
    """生成单条评论句的 Word 登记表

    Args:
        output_path: 输出文件路径
        record: 评论句记录
        metadata: 施评文献元数据
        index: 编号
        institution_info: 机构查询结果
        provider: 提供者信息

    Returns:
        输出文件路径
    """
    doc = Document()

    # 设置默认字体
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Times New Roman'
    font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')

    # 创建表格 (16行 x 5列)
    table = doc.add_table(rows=16, cols=5, style='Table Grid')
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    ep = record.被评文献
    inst = institution_info or {}

    # 合并施评文献信息列
    eval_institution = ep.第一作者机构 or inst.get("institution", "")
    eval_country = ep.第一作者国家 or inst.get("country", "")

    # ── 行0: 标题行 ──
    # 合并 A1:B1
    cell_a0 = table.cell(0, 0)
    cell_b0 = table.cell(0, 1)
    cell_a0.merge(cell_b0)
    _write_cell(cell_a0, f"{metadata.year}年学术评论句登记表", bold=True)

    # 合并 C1:E1
    cell_c0 = table.cell(0, 2)
    cell_e0 = table.cell(0, 4)
    cell_c0.merge(cell_e0)
    _write_cell(cell_c0, f"流水号：      /")

    # ── 定义行内容 ──
    # 施评文献作者（第一作者姓氏加粗）
    authors_str = metadata.authors_str

    # 期刊格式
    journal = metadata.journal_cn or metadata.journal_en
    vol_info = ""
    if metadata.volume:
        vol_info = f"{metadata.volume}"
        if metadata.issue:
            vol_info += f"({metadata.issue})"
    pages = metadata.pages
    journal_str = f"{journal}, {metadata.year}, {vol_info}: {pages}." if journal else ""

    row_definitions = [
        # (行号, 标题, 内容)
        (1, "施评文献作者", authors_str),
        (2, "期刊名称，年，卷，期，页", journal_str),
        (3, "机构中文名称", metadata.institution_cn),
        (4, "机构英文名称", metadata.institution_en or ""),
        (5, "中文题目", metadata.title_cn),
        (6, "英文题目", metadata.title_en),
        (7, "论文关键词", "/"),
        (8, "题目关键词", "/"),
        (9, "学术评论句", None),  # 特殊处理
        (10, "被评文献", _format_ref_for_word(record)),
        (11, "其他被评文献", ""),
        (12, "被评作者机构", f"{eval_institution}, {eval_country}" if eval_institution else ""),
        (13, "标志词", record.标志词),
        (14, "提供者", provider),
    ]

    for row_idx, title, content in row_definitions:
        # A列：标题
        _write_cell(table.cell(row_idx, 0), title, bold=True)

        # B-C列合并：内容
        cell_b = table.cell(row_idx, 1)
        cell_c = table.cell(row_idx, 2)
        cell_b.merge(cell_c)

        if row_idx == 9:
            # 学术评论句：标志词/作者/年份加粗
            _write_bold_sentence(
                cell_b,
                record.评论句原文,
                record.标志词,
                ep.第一作者,
                ep.年份,
            )
        else:
            _write_cell(cell_b, content or "")

        # D列：第1次反馈
        if row_idx in (1, 9, 10, 11):
            _write_cell(table.cell(row_idx, 3), "")
        else:
            _write_cell(table.cell(row_idx, 3), "/")

        # E列：第2次反馈
        if row_idx in (1, 9, 10, 11):
            _write_cell(table.cell(row_idx, 4), "")
        else:
            _write_cell(table.cell(row_idx, 4), "/")

    # ── 行15: 备注行 ──
    cell_all = table.cell(15, 0)
    cell_end = table.cell(15, 4)
    cell_all.merge(cell_end)
    _write_cell(cell_all, "其他：你这个评论句有（/）个被评文献，"
                "请提供另（/）张登记表。【注意重新提交修正后的Excel表、登记表及原文】")

    # 在行1中，给第一作者姓氏加粗
    # 需要重写行1的内容单元格
    if metadata.first_author:
        cell_author = table.cell(1, 1)
        _write_bold_author(cell_author, authors_str, metadata.first_author)

    doc.save(output_path)
    logger.info(f"Word 登记表已保存: {output_path}")
    return output_path


def _write_bold_author(cell, authors_str: str, first_author: str):
    """在作者列表中将第一作者加粗"""
    cell.text = ""
    paragraph = cell.paragraphs[0]

    if first_author in authors_str:
        idx = authors_str.index(first_author)
        # 前缀
        if idx > 0:
            run = paragraph.add_run(authors_str[:idx])
            run.font.name = "Times New Roman"
            run.font.size = Pt(10.5)
            run._element.rPr.rFonts.set(qn('w:eastAsia'), "Times New Roman")
        # 第一作者加粗
        run = paragraph.add_run(first_author)
        run.bold = True
        run.font.name = "Times New Roman"
        run.font.size = Pt(10.5)
        run._element.rPr.rFonts.set(qn('w:eastAsia'), "Times New Roman")
        # 后缀
        end_idx = idx + len(first_author)
        if end_idx < len(authors_str):
            run = paragraph.add_run(authors_str[end_idx:])
            run.font.name = "Times New Roman"
            run.font.size = Pt(10.5)
            run._element.rPr.rFonts.set(qn('w:eastAsia'), "Times New Roman")
    else:
        run = paragraph.add_run(authors_str)
        run.font.name = "Times New Roman"
        run.font.size = Pt(10.5)
        run._element.rPr.rFonts.set(qn('w:eastAsia'), "Times New Roman")
