"""处理管道 - 串联所有模块的完整流程"""

import logging
import os
import re
from pathlib import Path

from config import AppConfig
from core.llm_analyzer import AnalysisResult, CommentRecord, ReviewingPaper, call_llm
from core.pdf_parser import PaperMetadata, parse_pdf
from core.ref_parser import parse_references, find_reference_by_author_year
from core.pdf_highlighter import highlight_sentences
from core.excel_writer import write_excel
from core.word_writer import write_word
from core.institution_lookup import batch_lookup

logger = logging.getLogger(__name__)


def process_paper(
    pdf_path: str,
    config: AppConfig,
    provider: str = "",
    progress_callback=None,
) -> dict:
    """处理单篇文献的完整流程

    Args:
        pdf_path: PDF 文件路径
        config: 应用配置
        provider: 提供者信息
        progress_callback: 进度回调 fn(message: str)

    Returns:
        dict with keys:
            - records: list[CommentRecord]
            - excel_path: str
            - word_paths: list[str]
            - highlighted_pdf_path: str
            - metadata: PaperMetadata
            - log: list[str]  # 处理日志
    """
    log = []

    def _log(msg: str):
        log.append(msg)
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    # 准备输出目录
    pdf_name = Path(pdf_path).stem
    output_dir = os.path.join(config.output_dir, pdf_name)
    os.makedirs(output_dir, exist_ok=True)

    # ── 步骤1: PDF 解析 ──
    _log("步骤 1/7: 解析 PDF 文件...")
    parse_result = parse_pdf(pdf_path)
    metadata = parse_result.metadata
    _log(f"  - 提取 {parse_result.page_count} 页文本")
    _log(f"  - 施评文献: {metadata.first_author}, {metadata.title_cn or metadata.title_en}")
    _log(f"  - 期刊: {metadata.journal_cn}, {metadata.year}")

    # ── 步骤2: 解析参考文献 ──
    _log("步骤 2/7: 解析参考文献列表...")
    references = parse_references(parse_result.full_text)
    _log(f"  - 共解析 {len(references)} 条参考文献")
    journal_refs = [r for r in references if r.is_journal]
    _log(f"  - 其中期刊论文 {len(journal_refs)} 条")

    # ── 步骤3: 大模型分析 ──
    _log(f"步骤 3/7: 调用大模型 ({config.llm.model}) 分析...")
    analysis_result = call_llm(
        full_text=parse_result.full_text,
        authors=metadata.authors_str,
        config=config.llm,
        progress_callback=lambda msg: _log(f"  - {msg}"),
    )
    records = analysis_result.评论句记录
    _log(f"  - 识别到 {len(records)} 条学术评论句")

    # ── 步骤3.1: 用 LLM 提取的施评文献信息覆盖正则结果 ──
    reviewing = analysis_result.施评文献
    if reviewing.第一作者:
        _log("  - 使用 LLM 提取的施评文献元数据补充/覆盖正则结果")
        # 判断是中文还是英文作者
        _is_cn = any('\u4e00' <= c <= '\u9fff' for c in reviewing.第一作者)
        if _is_cn:
            metadata.first_author_cn = metadata.first_author_cn or reviewing.第一作者
            if reviewing.全部作者:
                all_authors = [a.strip() for a in reviewing.全部作者.split(",")]
                metadata.authors_cn = metadata.authors_cn or all_authors
        else:
            metadata.first_author_en = metadata.first_author_en or reviewing.第一作者
            if reviewing.全部作者:
                all_authors = [a.strip() for a in reviewing.全部作者.split(",")]
                metadata.authors_en = metadata.authors_en or all_authors
        metadata.title_cn = metadata.title_cn or (reviewing.文章名 if any('\u4e00' <= c <= '\u9fff' for c in reviewing.文章名) else "")
        metadata.title_en = metadata.title_en or (reviewing.文章名 if not any('\u4e00' <= c <= '\u9fff' for c in reviewing.文章名) else "")
        metadata.journal_cn = metadata.journal_cn or (reviewing.期刊名称 if any('\u4e00' <= c <= '\u9fff' for c in reviewing.期刊名称) else "")
        metadata.journal_en = metadata.journal_en or (reviewing.期刊名称 if not any('\u4e00' <= c <= '\u9fff' for c in reviewing.期刊名称) else "")
        metadata.year = metadata.year or reviewing.年份
        metadata.volume = metadata.volume or reviewing.卷
        metadata.issue = metadata.issue or reviewing.期
        metadata.pages = metadata.pages or reviewing.起止页码
        metadata.institution_cn = metadata.institution_cn or (reviewing.第一作者机构 if any('\u4e00' <= c <= '\u9fff' for c in reviewing.第一作者机构) else "")
        metadata.institution_en = metadata.institution_en or (reviewing.第一作者机构 if not any('\u4e00' <= c <= '\u9fff' for c in reviewing.第一作者机构) else "")
        metadata.country = metadata.country if metadata.country != "中国" else reviewing.第一作者国家 or "中国"
        _log(f"  - 施评文献: {metadata.first_author}, {metadata.title_cn or metadata.title_en}")

    # ── 步骤3.5: 后处理校验 ──
    # 用解析出的参考文献列表做二次校验，剔除非期刊论文
    if records and references:
        validated_records = []
        for record in records:
            author = record.被评文献.第一作者
            year = record.被评文献.年份
            matched_ref = find_reference_by_author_year(references, author, year)
            if matched_ref is None:
                _log(f"  - [保留] {author}({year}): 未在参考文献中匹配到，保留待人工审核")
                validated_records.append(record)
            elif not matched_ref.is_journal:
                _log(f"  - [剔除] {author}({year}): 参考文献[{matched_ref.index}]类型为"
                     f"[{matched_ref.ref_type}]，非期刊论文")
            else:
                _log(f"  - [保留] {author}({year}): 匹配参考文献[{matched_ref.index}]，"
                     f"期刊={matched_ref.journal}")
                # 补充大模型可能遗漏的信息
                if not record.被评文献.期刊名称 and matched_ref.journal:
                    record.被评文献.期刊名称 = matched_ref.journal
                if not record.被评文献.卷 and matched_ref.volume:
                    record.被评文献.卷 = matched_ref.volume
                if not record.被评文献.期 and matched_ref.issue:
                    record.被评文献.期 = matched_ref.issue
                if not record.被评文献.起止页码 and matched_ref.pages:
                    record.被评文献.起止页码 = matched_ref.pages
                validated_records.append(record)

        if len(validated_records) < len(records):
            _log(f"  - 后处理校验: {len(records)} → {len(validated_records)} 条"
                 f"（剔除 {len(records) - len(validated_records)} 条非期刊文献）")
        records = validated_records

    if not records:
        _log("未找到符合条件的学术评论句，流程结束。")
        return {
            "records": [],
            "excel_path": "",
            "word_paths": [],
            "highlighted_pdf_path": "",
            "metadata": metadata,
            "log": log,
        }

    # ── 步骤4: 查询机构信息 ──
    _log("步骤 4/7: 查询被评文献作者机构信息...")
    papers_to_lookup = []
    for r in records:
        papers_to_lookup.append({
            "title": r.被评文献.文章名,
            "first_author": r.被评文献.第一作者,
            "year": r.被评文献.年份,
        })
    institution_results = batch_lookup(
        papers_to_lookup,
        progress_callback=lambda msg: _log(f"  - {msg}"),
    )
    for i, inst in enumerate(institution_results):
        if inst.get("institution"):
            _log(f"  - [{i+1}] 机构: {inst['institution']}, 国家: {inst['country']}")
        else:
            _log(f"  - [{i+1}] 未查询到机构信息")

    # ── 步骤5: PDF 高亮 ──
    _log("步骤 5/7: 在 PDF 中高亮标记评论句...")
    highlighted_pdf_path = os.path.join(output_dir, f"{pdf_name}_高亮标注.pdf")
    sentences = [r.评论句原文 for r in records]
    highlighted_count = highlight_sentences(
        pdf_path, highlighted_pdf_path, sentences,
        progress_callback=lambda msg: _log(f"  - {msg}"),
    )
    _log(f"  - 成功高亮 {highlighted_count}/{len(sentences)} 条句子")

    # ── 步骤6: 生成 Excel ──
    _log("步骤 6/7: 生成 Excel 汇总表...")
    excel_path = os.path.join(output_dir, f"{pdf_name}_汇总表.xlsx")
    write_excel(excel_path, records, metadata, institution_results, provider)
    _log(f"  - 已保存: {excel_path}")

    # ── 步骤7: 生成 Word 登记表 ──
    _log("步骤 7/7: 生成 Word 登记表...")
    word_paths = []
    for i, record in enumerate(records):
        word_path = os.path.join(output_dir, f"{pdf_name}_登记表_{i+1}.docx")
        inst = institution_results[i] if i < len(institution_results) else None
        write_word(word_path, record, metadata, i + 1, inst, provider)
        word_paths.append(word_path)
        _log(f"  - 已保存: {word_path}")

    _log(f"处理完成！共 {len(records)} 条评论句，结果保存在: {output_dir}")

    return {
        "records": records,
        "excel_path": excel_path,
        "word_paths": word_paths,
        "highlighted_pdf_path": highlighted_pdf_path,
        "metadata": metadata,
        "log": log,
    }
