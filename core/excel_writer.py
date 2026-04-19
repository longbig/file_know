"""Excel 写入模块

职责：
- 按 26 列格式生成 .xlsx 汇总表
- 包含施评文献信息 + 评论句 + 被评文献信息
"""

import logging

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from core.llm_analyzer import CommentRecord
from core.pdf_parser import PaperMetadata

logger = logging.getLogger(__name__)

# 26 列表头
HEADERS = [
    "编号",
    "施评文献全部信息",
    "施评文献第一作者",
    "施评文献其他作者",
    "施评文章名",
    "施评期刊年卷期页码",
    "施评期刊",
    "施评年份",
    "施评卷期起止页码",
    "施评文献第一作者机构",
    "施评国家（第一作者）",
    "评论句",
    "标志词",
    "被评文献全部信息",
    "被评文献第一作者",
    "被评文献其他作者",
    "被评文章名",
    "被评期刊年卷期起止页码",
    "被评期刊全称",
    "被评年份",
    "被评卷期起止页码",
    "被评文献第一作者机构",
    "被评国家（第一作者）",
    "其他被评文献",
    "提供者",
    "备注",
]


def _format_citation(meta: PaperMetadata) -> str:
    """格式化施评文献的完整引用信息（GB/T 7714 格式）"""
    authors = ", ".join(meta.authors_cn) if meta.authors_cn else ", ".join(meta.authors_en)
    title = meta.title_cn or meta.title_en
    journal = meta.journal_cn or meta.journal_en

    result = f"{authors}. {title}[J]. {journal}, {meta.year}"
    if meta.volume:
        result += f", {meta.volume}"
        if meta.issue:
            result += f"({meta.issue})"
    if meta.pages:
        result += f": {meta.pages}"
    result += "."
    return result


def _format_vol_issue_pages(meta: PaperMetadata) -> str:
    """格式化卷期页码"""
    result = ""
    if meta.volume:
        result = meta.volume
        if meta.issue:
            result += f"({meta.issue})"
    if meta.pages:
        result += f": {meta.pages}"
    return result


def _format_journal_year_vol(meta: PaperMetadata) -> str:
    """格式化期刊年卷期页码"""
    journal = meta.journal_cn or meta.journal_en
    result = journal
    if meta.year:
        result += f", {meta.year}"
    vol_issue = _format_vol_issue_pages(meta)
    if vol_issue:
        result += f", {vol_issue}"
    result += "."
    return result


def _format_evaluated_ref(record: CommentRecord) -> str:
    """格式化被评文献的完整引用（GB/T 7714 格式）"""
    ep = record.被评文献
    authors = ", ".join(ep.全部作者列表) if ep.全部作者列表 else ep.第一作者
    result = authors
    if ep.文章名:
        result += f". {ep.文章名}[J]."
    if ep.期刊名称:
        result += f" {ep.期刊名称},"
    if ep.年份:
        result += f" {ep.年份},"
    vol = ""
    if ep.卷:
        vol = ep.卷
        if ep.期:
            vol += f"({ep.期})"
    if vol:
        result += f" {vol}"
    if ep.起止页码:
        result += f": {ep.起止页码}"
    result += "."
    return result


def _format_evaluated_vol(record: CommentRecord) -> str:
    """格式化被评文献的期刊年卷期页码"""
    ep = record.被评文献
    result = ep.期刊名称 or ""
    if ep.年份:
        result += f", {ep.年份}"
    vol = ""
    if ep.卷:
        vol = ep.卷
        if ep.期:
            vol += f"({ep.期})"
    if vol:
        result += f", {vol}"
    if ep.起止页码:
        result += f": {ep.起止页码}"
    result += "."
    return result


def _format_evaluated_vol_pages(record: CommentRecord) -> str:
    """格式化被评文献的卷期起止页码"""
    ep = record.被评文献
    vol = ""
    if ep.卷:
        vol = ep.卷
        if ep.期:
            vol += f"({ep.期})"
    if ep.起止页码:
        vol += f": {ep.起止页码}"
    return vol


def write_excel(
    output_path: str,
    records: list[CommentRecord],
    metadata: PaperMetadata,
    institution_results: list[dict] | None = None,
    provider: str = "",
) -> str:
    """生成 Excel 汇总表

    Args:
        output_path: 输出文件路径
        records: 评论句记录列表
        metadata: 施评文献元数据
        institution_results: 机构查询结果列表（与 records 一一对应）
        provider: 提供者信息

    Returns:
        输出文件路径
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"

    # 样式定义
    header_font = Font(name="Times New Roman", bold=True, size=11)
    header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    cell_font = Font(name="Times New Roman", size=11)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    wrap_alignment = Alignment(wrap_text=True, vertical="center")

    # 写入表头
    for col, header in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border
        cell.alignment = wrap_alignment

    # 预计算施评文献信息（每行相同）
    citation = _format_citation(metadata)
    journal_year_vol = _format_journal_year_vol(metadata)
    vol_issue_pages = _format_vol_issue_pages(metadata)

    # 写入数据行
    for i, record in enumerate(records):
        row = i + 2
        ep = record.被评文献

        # 机构查询结果
        inst = {}
        if institution_results and i < len(institution_results):
            inst = institution_results[i]

        # 被评作者机构和国家：优先用 LLM 返回的，其次用 CrossRef 查到的
        eval_institution = ep.第一作者机构 or inst.get("institution", "")
        eval_country = ep.第一作者国家 or inst.get("country", "")

        row_data = [
            i + 1,  # 编号
            citation,  # 施评文献全部信息
            metadata.first_author,  # 施评文献第一作者
            metadata.other_authors,  # 施评文献其他作者
            metadata.title_cn or metadata.title_en,  # 施评文章名
            journal_year_vol,  # 施评期刊年卷期页码
            metadata.journal_cn or metadata.journal_en,  # 施评期刊
            metadata.year,  # 施评年份
            vol_issue_pages,  # 施评卷期起止页码
            metadata.institution_cn or metadata.institution_en,  # 施评机构
            metadata.country,  # 施评国家
            record.评论句原文,  # 评论句
            record.标志词,  # 标志词
            _format_evaluated_ref(record),  # 被评文献全部信息
            ep.第一作者,  # 被评第一作者
            ep.其他作者,  # 被评其他作者
            ep.文章名,  # 被评文章名
            _format_evaluated_vol(record),  # 被评期刊年卷期页码
            ep.期刊名称,  # 被评期刊全称
            ep.年份,  # 被评年份
            _format_evaluated_vol_pages(record),  # 被评卷期起止页码
            eval_institution,  # 被评机构
            eval_country,  # 被评国家
            "",  # 其他被评文献
            provider,  # 提供者
            "",  # 备注
        ]

        for col, value in enumerate(row_data, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.font = cell_font
            cell.border = thin_border
            cell.alignment = wrap_alignment

    # 调整列宽
    col_widths = {
        1: 6, 2: 40, 3: 12, 4: 25, 5: 30,
        6: 30, 7: 15, 8: 8, 9: 18, 10: 25,
        11: 10, 12: 50, 13: 12, 14: 40, 15: 12,
        16: 25, 17: 30, 18: 30, 19: 15, 20: 8,
        21: 18, 22: 25, 23: 10, 24: 30, 25: 20, 26: 15,
    }
    for col, width in col_widths.items():
        ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = width

    wb.save(output_path)
    logger.info(f"Excel 汇总表已保存: {output_path}")
    return output_path
