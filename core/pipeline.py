"""处理管道 - 新 5 阶段架构

阶段 1：文本预处理层（纯 Python）
  - PDF 解析 → 全文 + 按句分割
  - 参考文献解析 → 结构化 Reference 列表
  - 施评文献元数据提取
  - 作者标准化（用于自引检测）

阶段 2：规则引擎层（纯 Python，零 LLM）
  - 标志词 regex 扫描 → 候选句标记
  - 三要素校验（标志词+作者+年份）
  - 一票否决规则过滤
  - 多文献拆分 / independently 拆分
  - 输出：候选评论句列表

阶段 3：LLM 语义判定层（轻量 Prompt）
  - 判断：是否为真正的学术评论句
  - 判断：标志词是否描述学术贡献事件
  - 输出：accept / reject + 理由

阶段 4：结果组装层（纯 Python）
  - 合并 LLM 判定结果
  - 从 Reference 补全被评文献字段
  - 机构查询（CrossRef 三级回退）

阶段 5：输出生成层
  - PDF 高亮
  - Excel 汇总表
  - Word 登记表
"""

import logging
import os
import shutil
from pathlib import Path

from config import AppConfig
from core.llm_analyzer import (
    AnalysisResult,
    CommentRecord,
    ReviewingPaper,
    call_llm,
    judge_candidates,
)
from core.pdf_parser import PaperMetadata, parse_pdf
from core.ref_parser import parse_references, find_reference_by_author_year
from core.sentence_splitter import split_sentences
from core.rule_engine import extract_candidates, normalize_authors
from core.result_assembler import assemble_results
from core.pdf_highlighter import highlight_sentences
from core.excel_writer import write_excel
from core.word_writer import write_word
from core.institution_lookup import lookup_institution, lookup_full_metadata

logger = logging.getLogger(__name__)


def process_paper(
    pdf_path: str,
    config: AppConfig,
    provider: str = "",
    progress_callback=None,
) -> dict:
    """处理单篇文献的完整流程（新 5 阶段架构）

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
            - institution_results: list[dict]
            - log: list[str]
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

    # ══════════════════════════════════════════════════════════
    # 阶段 1：文本预处理
    # ══════════════════════════════════════════════════════════
    _log("阶段 1/5: 文本预处理...")

    # 1.1 PDF 解析
    _log("  [1.1] 解析 PDF 文件...")
    parse_result = parse_pdf(pdf_path)
    metadata = parse_result.metadata
    _log(f"  - 提取 {parse_result.page_count} 页文本")
    _log(f"  - 施评文献: {metadata.first_author}, {metadata.title_cn or metadata.title_en}")

    # 清理 PDF 元数据中的 Unicode 残留
    if metadata.institution_en:
        metadata.institution_en = metadata.institution_en.replace('\u2011', '-')
    if metadata.institution_cn:
        metadata.institution_cn = metadata.institution_cn.replace('\u2011', '-')

    # 1.2 参考文献解析
    _log("  [1.2] 解析参考文献列表...")
    references = parse_references(parse_result.full_text)
    journal_refs = [r for r in references if r.is_journal]
    _log(f"  - 共 {len(references)} 条参考文献，其中期刊 {len(journal_refs)} 条")

    # 1.3 句子分割
    _log("  [1.3] 分割句子...")
    sentences = split_sentences(parse_result.full_text)
    _log(f"  - 分割为 {len(sentences)} 个句子")

    # 1.4 作者标准化（自引检测用）
    self_authors = normalize_authors(metadata.authors_str)
    _log(f"  - 施评文献作者: {self_authors}")

    # ══════════════════════════════════════════════════════════
    # 阶段 2：规则引擎
    # ══════════════════════════════════════════════════════════
    _log("阶段 2/5: 规则引擎筛选...")

    filter_log: list[str] = []  # 过滤过程详细日志

    candidates = extract_candidates(
        sentences=sentences,
        references=references,
        self_authors=self_authors,
        progress_callback=lambda msg: _log(f"  - {msg}"),
        filter_log=filter_log,
    )
    _log(f"  - 规则引擎输出 {len(candidates)} 条候选评论句")

    if not candidates:
        _log("规则引擎未找到候选评论句，流程结束。")
        filter_log_path = os.path.join(output_dir, f"{pdf_name}_过滤日志.txt")
        _write_filter_log(filter_log_path, pdf_name, filter_log, len(sentences), [], [])
        output_dir = _move_to_category(output_dir, config.output_dir, pdf_name, has_result=False)
        return _empty_result(metadata, log)

    # ══════════════════════════════════════════════════════════
    # 阶段 3：LLM 语义判定
    # ══════════════════════════════════════════════════════════
    _log(f"阶段 3/5: LLM 语义判定 ({config.llm.model})...")

    judge_results = judge_candidates(
        candidates=candidates,
        config=config.llm,
        self_authors_str=metadata.authors_str,
        progress_callback=lambda msg: _log(f"  - {msg}"),
    )

    # 筛选通过的候选句
    accepted_candidates = []
    accepted_judge_results = []
    for i, (candidate, judge) in enumerate(zip(candidates, judge_results)):
        if judge.accept:
            accepted_candidates.append(candidate)
            accepted_judge_results.append(judge)
            _log(f"  - [通过] #{i+1} {candidate.author_name}({candidate.year}): {judge.reason}")
            filter_log.append(f"[LLM 通过] #{i+1} 作者={candidate.author_name} 年份={candidate.year} 理由={judge.reason}")
        else:
            _log(f"  - [否决] #{i+1} {candidate.author_name}({candidate.year}): {judge.reason}")
            filter_log.append(f"[LLM 否决] #{i+1} 作者={candidate.author_name} 年份={candidate.year} 理由={judge.reason}")

    _log(f"  - 语义判定: {len(candidates)} 条 → {len(accepted_candidates)} 条通过")

    if not accepted_candidates:
        _log("语义判定后无通过的评论句，流程结束。")
        filter_log_path = os.path.join(output_dir, f"{pdf_name}_过滤日志.txt")
        _write_filter_log(filter_log_path, pdf_name, filter_log, len(sentences), candidates, judge_results)
        output_dir = _move_to_category(output_dir, config.output_dir, pdf_name, has_result=False)
        return _empty_result(metadata, log)

    # ══════════════════════════════════════════════════════════
    # 阶段 4：结果组装
    # ══════════════════════════════════════════════════════════
    _log("阶段 4/5: 结果组装与机构查询...")

    # 4.1 组装结果
    analysis_result = assemble_results(
        accepted_candidates=accepted_candidates,
        judge_results=accepted_judge_results,
        references=references,
        metadata=metadata,
    )
    records = analysis_result.评论句记录

    # 4.2 用 LLM 返回的施评文献信息补充正则结果
    reviewing = analysis_result.施评文献
    _merge_reviewing_metadata(reviewing, metadata)

    # 4.3 机构查询 + CrossRef 元数据补全
    _log("  [4.3] 查询被评文献作者机构 + 补全元数据...")
    institution_results = []
    for i, r in enumerate(records):
        ep = r.被评文献
        inst_info = {"institution": "", "country": "", "doi": ""}

        # 获取 DOI
        ref_doi = ""
        candidate = accepted_candidates[i] if i < len(accepted_candidates) else None
        if candidate and candidate.matched_ref and candidate.matched_ref.doi:
            ref_doi = candidate.matched_ref.doi

        # 用 CrossRef 获取完整元数据（作者列表+期号+机构）
        full_meta = lookup_full_metadata(
            doi=ref_doi,
            title=ep.文章名,
            year=ep.年份,
            first_author=ep.第一作者,
        )

        # 补全作者列表（仅在现有列表不完整时 — et al. 截断）
        if full_meta["authors"] and len(full_meta["authors"]) > len(ep.全部作者列表):
            _log(f"  - [{i+1}] CrossRef 补全作者: {len(ep.全部作者列表)} → {len(full_meta['authors'])}")
            ep.全部作者列表 = full_meta["authors"]
            if len(full_meta["authors"]) > 1:
                ep.其他作者 = ", ".join(full_meta["authors"][1:])

        # 补全文章标题（CrossRef 标题通常更准确，修复 PDF 解析的引号和连字符问题）
        if full_meta["title"] and ep.文章名:
            from core.institution_lookup import _title_similarity
            sim = _title_similarity(ep.文章名, full_meta["title"])
            if sim >= 0.5:  # 高相似度时才替换
                ep.文章名 = full_meta["title"]

        # 补全期号
        if full_meta["issue"] and not ep.期:
            _log(f"  - [{i+1}] CrossRef 补全期号: {full_meta['issue']}")
            ep.期 = full_meta["issue"]

        # 补全卷号（参考文献可能缺失）
        if full_meta["volume"] and not ep.卷:
            ep.卷 = full_meta["volume"]

        # 补全页码
        if full_meta["pages"] and not ep.起止页码:
            ep.起止页码 = full_meta["pages"]

        # 补全机构信息
        if full_meta["institution"]:
            if not ep.第一作者机构:
                ep.第一作者机构 = full_meta["institution"]
                ep.第一作者国家 = full_meta["country"]
                _log(f"  - [{i+1}] CrossRef 机构: {full_meta['institution'][:50]}, {full_meta['country']}")
            inst_info["institution"] = ep.第一作者机构
            inst_info["country"] = ep.第一作者国家
        elif ep.第一作者机构:
            _log(f"  - [{i+1}] 已有机构: {ep.第一作者机构}, {ep.第一作者国家}")
            inst_info["institution"] = ep.第一作者机构
            inst_info["country"] = ep.第一作者国家
        else:
            _log(f"  - [{i+1}] 未查询到机构信息")

        institution_results.append(inst_info)

    # ══════════════════════════════════════════════════════════
    # 写入过滤日志文件
    # ══════════════════════════════════════════════════════════
    filter_log_path = os.path.join(output_dir, f"{pdf_name}_过滤日志.txt")
    _write_filter_log(filter_log_path, pdf_name, filter_log, len(sentences), candidates, judge_results)

    # ══════════════════════════════════════════════════════════
    # 阶段 5：输出生成
    # ══════════════════════════════════════════════════════════
    _log("阶段 5/5: 生成输出文件...")

    # 5.1 PDF 高亮
    _log("  [5.1] PDF 高亮标记...")
    highlighted_pdf_path = os.path.join(output_dir, f"{pdf_name}_高亮标注.pdf")
    highlighted_count = highlight_sentences(
        pdf_path, highlighted_pdf_path,
        records=records,
        references=references,
        metadata=metadata,
        progress_callback=lambda msg: _log(f"  - {msg}"),
    )
    _log(f"  - 成功高亮 {highlighted_count}/{len(records)} 条评论句")

    # 5.2 Excel 汇总表
    _log("  [5.2] 生成 Excel 汇总表...")
    excel_path = os.path.join(output_dir, f"{pdf_name}_汇总表.xlsx")
    write_excel(excel_path, records, metadata, institution_results, provider)
    _log(f"  - 已保存: {excel_path}")

    # 5.3 Word 登记表
    _log("  [5.3] 生成 Word 登记表...")
    word_paths = []
    for i, record in enumerate(records):
        word_path = os.path.join(output_dir, f"{pdf_name}_登记表_{i+1}.docx")
        inst = institution_results[i] if i < len(institution_results) else None
        write_word(word_path, record, metadata, i + 1, inst, provider)
        word_paths.append(word_path)
        _log(f"  - 已保存: {word_path}")

    _log(f"处理完成！共 {len(records)} 条评论句，结果保存在: {output_dir}")

    # 将输出目录移动到"有结果"分类子目录
    new_output_dir = _move_to_category(output_dir, config.output_dir, pdf_name, has_result=True)
    # 更新文件路径
    if new_output_dir != output_dir:
        excel_path = excel_path.replace(output_dir, new_output_dir)
        highlighted_pdf_path = highlighted_pdf_path.replace(output_dir, new_output_dir)
        word_paths = [p.replace(output_dir, new_output_dir) for p in word_paths]

    return {
        "records": records,
        "excel_path": excel_path,
        "word_paths": word_paths,
        "highlighted_pdf_path": highlighted_pdf_path,
        "metadata": metadata,
        "institution_results": institution_results,
        "log": log,
    }


def _move_to_category(output_dir: str, base_output_dir: str, pdf_name: str, has_result: bool) -> str:
    """将输出目录移动到"有结果"或"无结果"子目录"""
    category = "有结果" if has_result else "无结果"
    dest_parent = os.path.join(base_output_dir, category)
    os.makedirs(dest_parent, exist_ok=True)
    dest = os.path.join(dest_parent, pdf_name)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.move(output_dir, dest)
    return dest


def _empty_result(metadata: PaperMetadata, log: list[str]) -> dict:
    """返回空结果"""
    return {
        "records": [],
        "excel_path": "",
        "word_paths": [],
        "highlighted_pdf_path": "",
        "metadata": metadata,
        "institution_results": [],
        "log": log,
    }


def _write_filter_log(
    path: str,
    pdf_name: str,
    filter_log: list[str],
    total_sentences: int,
    candidates: list,
    judge_results: list,
) -> None:
    """将过滤过程写入文本文件"""
    from datetime import datetime
    accepted = sum(1 for j in judge_results if j.accept)
    rejected = len(judge_results) - accepted

    lines = [
        f"过滤日志 — {pdf_name}",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 60,
        f"总句数：{total_sentences}",
        f"规则引擎候选：{len(candidates)} 条",
        f"LLM 通过：{accepted} 条  否决：{rejected} 条",
        "=" * 60,
        "",
    ] + filter_log

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _merge_reviewing_metadata(reviewing: ReviewingPaper, metadata: PaperMetadata):
    """将组装的施评文献信息与 PDF 正则结果合并

    以 PDF 正则结果为主，缺失的字段用组装结果补充。
    """
    if not reviewing.第一作者:
        return

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

    metadata.title_cn = metadata.title_cn or (
        reviewing.文章名 if any('\u4e00' <= c <= '\u9fff' for c in reviewing.文章名) else ""
    )
    metadata.title_en = metadata.title_en or (
        reviewing.文章名 if not any('\u4e00' <= c <= '\u9fff' for c in reviewing.文章名) else ""
    )
    metadata.journal_cn = metadata.journal_cn or (
        reviewing.期刊名称 if any('\u4e00' <= c <= '\u9fff' for c in reviewing.期刊名称) else ""
    )
    metadata.journal_en = metadata.journal_en or (
        reviewing.期刊名称 if not any('\u4e00' <= c <= '\u9fff' for c in reviewing.期刊名称) else ""
    )
    metadata.year = metadata.year or reviewing.年份
    metadata.volume = metadata.volume or reviewing.卷
    metadata.issue = metadata.issue or reviewing.期
    metadata.pages = metadata.pages or reviewing.起止页码
    metadata.institution_cn = metadata.institution_cn or (
        reviewing.第一作者机构 if any('\u4e00' <= c <= '\u9fff' for c in reviewing.第一作者机构) else ""
    )
    metadata.institution_en = metadata.institution_en or (
        reviewing.第一作者机构 if not any('\u4e00' <= c <= '\u9fff' for c in reviewing.第一作者机构) else ""
    )
    metadata.country = metadata.country if metadata.country != "中国" else reviewing.第一作者国家 or "中国"
