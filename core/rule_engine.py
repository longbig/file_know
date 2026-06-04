"""规则引擎主流程

职责：
- 串联标志词匹配→三要素校验→一票否决→多文献拆分
- 纯 Python 确定性逻辑，零 LLM 调用
- 输出候选评论句列表，供 LLM 语义判定层做最终判定
"""

import logging
from core.sentence_splitter import Sentence, split_sentences
from core.marker_matcher import match_markers
from core.author_extractor import extract_authors
from core.year_extractor import extract_years
from core.veto_rules import apply_veto_rules, VetoResult
from core.record_splitter import (
    CandidateRecord,
    split_independently,
    split_multiple_refs,
)
from core.ref_parser import Reference, find_reference_by_author_year

logger = logging.getLogger(__name__)


def extract_candidates(
    sentences: list[Sentence],
    references: list[Reference],
    self_authors: set[str],
    progress_callback=None,
    filter_log: list[str] | None = None,
) -> list[CandidateRecord]:
    """规则引擎主流程：从句子列表中提取候选评论句

    纯规则筛选，不调用 LLM。
    执行步骤：
    1. 标志词匹配
    2. 作者+年份提取
    3. 三要素校验（标志词+作者+年份必须同时存在）
    4. 一票否决规则过滤
    5. 参考文献匹配
    6. 多文献/independently 拆分

    Args:
        sentences: 句子列表（由 sentence_splitter 生成）
        references: 参考文献列表（由 ref_parser 生成）
        self_authors: 施评文献作者名称集合（用于自引检测）
        progress_callback: 进度回调

    Returns:
        候选评论句记录列表
    """
    if not sentences:
        logger.warning("输入句子列表为空")
        return []

    candidates: list[CandidateRecord] = []
    stats = {
        "total_sentences": len(sentences),
        "has_marker": 0,
        "has_three_elements": 0,
        "passed_veto": 0,
        "final_records": 0,
    }

    for sent in sentences:
        # ── Step 1: 标志词匹配 ──
        markers = match_markers(sent.text)
        if not markers:
            continue
        stats["has_marker"] += 1

        # ── Step 2: 提取作者和年份 ──
        authors = extract_authors(sent.text)
        years = extract_years(sent.text)

        # ── Step 3: 三要素校验 ──
        # 需要同时有标志词、作者、年份
        if not authors:
            logger.debug(f"句子[{sent.index}]：有标志词但无作者，跳过")
            if filter_log is not None:
                filter_log.append(f"[三要素缺作者] 句#{sent.index} 标志词={[m.marker for m in markers]} | {sent.text[:80]}")
            continue
        if not years:
            logger.debug(f"句子[{sent.index}]：有标志词但无年份，跳过")
            if filter_log is not None:
                filter_log.append(f"[三要素缺年份] 句#{sent.index} 标志词={[m.marker for m in markers]} | {sent.text[:80]}")
            continue
        stats["has_three_elements"] += 1

        # ── Step 4: 参考文献匹配 ──
        # 策略：优先用引用编号 [N] 直接匹配，其次用作者+年份搜索
        matched_refs: list[Reference] = []
        matched_ref_ids: set[int] = set()  # 避免重复

        # 建立参考文献索引映射
        ref_by_index: dict[int, Reference] = {r.index: r for r in references}

        # 4a: 优先通过作者关联的引用编号直接匹配
        for author in authors:
            if author.ref_numbers:
                for ref_num in author.ref_numbers:
                    if ref_num in ref_by_index and ref_num not in matched_ref_ids:
                        matched_refs.append(ref_by_index[ref_num])
                        matched_ref_ids.add(ref_num)

        # 4b: 如果引用编号没有匹配到，回退到作者+年份匹配
        if not matched_refs:
            # 从句子中提取所有引用编号（全局），作为备选
            import re as _re
            all_ref_nums_in_sentence = []
            for m in _re.finditer(r'\[(\d+(?:\s*[,，\-]\s*\d+)*)\]', sent.text):
                nums = _re.findall(r'\d+', m.group(1))
                all_ref_nums_in_sentence.extend(int(n) for n in nums)

            # 如果句中只有一个引用编号，直接使用
            if len(all_ref_nums_in_sentence) == 1:
                ref_num = all_ref_nums_in_sentence[0]
                if ref_num in ref_by_index and ref_num not in matched_ref_ids:
                    matched_refs.append(ref_by_index[ref_num])
                    matched_ref_ids.add(ref_num)

            # 否则用作者+年份搜索
            if not matched_refs:
                for author in authors:
                    for year in years:
                        if year.is_ref_number:
                            continue
                        year_str = year.year
                        if year.is_decade:
                            year_str = year_str.rstrip('s')

                        ref = find_reference_by_author_year(
                            references, author.name, year_str
                        )
                        if ref and ref.index not in matched_ref_ids:
                            matched_refs.append(ref)
                            matched_ref_ids.add(ref.index)

        # 取第一个匹配的参考文献用于否决规则检查
        primary_ref = matched_refs[0] if matched_refs else None

        # ── Step 5: 一票否决规则 ──
        veto_result = apply_veto_rules(
            text=sent.text,
            author_mentions=authors,
            year_mentions=years,
            marker_matches=markers,
            self_authors=self_authors,
            matched_ref=primary_ref,
        )

        if veto_result.vetoed:
            logger.debug(
                f"句子[{sent.index}]被否决（规则#{veto_result.rule_id}）: "
                f"{veto_result.reason}"
            )
            if filter_log is not None:
                filter_log.append(
                    f"[规则否决] 句#{sent.index} 规则#{veto_result.rule_id} {veto_result.reason} | {sent.text[:80]}"
                )
            continue
        stats["passed_veto"] += 1
        if filter_log is not None:
            marker_names = [m.marker for m in markers]
            author_names = [a.name for a in authors]
            year_vals = [y.year for y in years]
            filter_log.append(
                f"[规则通过] 句#{sent.index} 标志词={marker_names} 作者={author_names} 年份={year_vals} | {sent.text[:80]}"
            )

        # ── Step 6: 选择最佳标志词 ──
        # 优先使用非裸词、最长的标志词
        best_marker = _select_best_marker(markers)

        # ── Step 7: 多文献/independently 拆分 ──
        # 过滤非期刊参考文献
        journal_refs = [r for r in matched_refs if r.is_journal]

        # 尝试 independently 拆分
        indie_records = split_independently(
            sentence_text=sent.text,
            marker=best_marker,
            author_mentions=authors,
            year_mentions=years,
            matched_refs=journal_refs,
            sentence_index=sent.index,
            prev_sentence=sent.prev_sentence,
            next_sentence=sent.next_sentence,
            marker_matches=markers,
        )

        if indie_records:
            candidates.extend(indie_records)
            stats["final_records"] += len(indie_records)
        else:
            # 普通多文献拆分
            records = split_multiple_refs(
                sentence_text=sent.text,
                marker=best_marker,
                author_mentions=authors,
                year_mentions=years,
                matched_refs=journal_refs if journal_refs else matched_refs,
                sentence_index=sent.index,
                prev_sentence=sent.prev_sentence,
                next_sentence=sent.next_sentence,
                marker_matches=markers,
            )
            candidates.extend(records)
            stats["final_records"] += len(records)

    # 输出统计信息
    logger.info(
        f"规则引擎统计: "
        f"总句数={stats['total_sentences']}, "
        f"有标志词={stats['has_marker']}, "
        f"三要素齐全={stats['has_three_elements']}, "
        f"通过否决={stats['passed_veto']}, "
        f"最终记录={stats['final_records']}"
    )

    if progress_callback:
        progress_callback(
            f"规则引擎筛选完成：{stats['total_sentences']} 句 → "
            f"{stats['final_records']} 条候选"
        )

    return candidates


def _select_best_marker(markers: list) -> str:
    """从标志词匹配列表中选择最佳标志词

    优先级：
    1. 非裸词优先
    2. 同类中选最长的

    Args:
        markers: MarkerMatch 列表

    Returns:
        最佳标志词字符串
    """
    # 先尝试找非裸词
    non_bare = [m for m in markers if not m.is_bare_word]
    if non_bare:
        # 按长度降序，选最长的
        non_bare.sort(key=lambda m: len(m.marker), reverse=True)
        return non_bare[0].marker

    # 全部是裸词，选最长的
    markers_sorted = sorted(markers, key=lambda m: len(m.marker), reverse=True)
    return markers_sorted[0].marker if markers_sorted else ""


def normalize_authors(authors_str: str) -> set[str]:
    """标准化施评文献作者列表，用于自引检测

    Args:
        authors_str: 作者列表字符串（逗号分隔）

    Returns:
        标准化后的作者名集合
    """
    if not authors_str:
        return set()

    authors = set()
    # 按逗号、分号分割
    raw_list = authors_str.replace(";", ",").split(",")

    for name in raw_list:
        name = name.strip()
        if not name:
            continue

        authors.add(name)

        # 英文名提取姓氏
        # 格式1: "Xinhai Yuan" → "Yuan"
        # 格式2: "YUAN Xinhai" → "YUAN" → "Yuan"
        parts = name.split()
        if len(parts) >= 2:
            # 如果是全大写，可能是 "YUAN" 格式的姓氏
            if parts[0].isupper() and len(parts[0]) > 1:
                authors.add(parts[0].title())
                authors.add(parts[0])
            else:
                # 常规格式 "Xinhai Yuan"，最后一个是姓氏
                authors.add(parts[-1])
                authors.add(parts[-1].title())

        # 中文名直接加入
        if any('\u4e00' <= c <= '\u9fff' for c in name):
            authors.add(name)

    return authors
