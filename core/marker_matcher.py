"""标志词预编译正则匹配模块。

从 markers.json 加载标志词列表，预编译正则表达式，
在学术评论句中匹配所有标志词及其位置。
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class MarkerMatch:
    """单个标志词匹配结果。"""
    marker: str          # 匹配到的标志词原文
    start: int           # 在句中的起始位置
    end: int             # 结束位置
    is_bare_word: bool   # 是否为裸词（需要和 first/firstly 等组合才有效）


# ---------------------------------------------------------------------------
# 模块级变量：预编译正则缓存
# ---------------------------------------------------------------------------

# 英文标志词：[(compiled_pattern, original_marker, is_bare_word), ...]
_english_patterns: list[tuple[re.Pattern, str, bool]] = []
# 中文标志词：[(compiled_pattern, original_marker), ...]
_chinese_patterns: list[tuple[re.Pattern, str]] = []
# 裸词集合（小写），用于快速判断
_bare_words_set: set[str] = set()
# 是否已加载
_loaded: bool = False


# ---------------------------------------------------------------------------
# 加载与预编译
# ---------------------------------------------------------------------------

def load_markers(markers_path: str | None = None) -> None:
    """加载 markers.json 并预编译正则表达式。

    Args:
        markers_path: markers.json 的路径，默认为项目根目录下的 markers.json。
    """
    global _english_patterns, _chinese_patterns, _bare_words_set, _loaded

    if markers_path is None:
        # 当前文件: core/marker_matcher.py → 父目录的父目录 = 项目根目录
        project_root = Path(__file__).parent.parent
        markers_path = str(project_root / "markers.json")

    logger.info("加载标志词文件: %s", markers_path)

    with open(markers_path, "r", encoding="utf-8") as f:
        data: dict = json.load(f)

    english_markers: list[str] = data.get("english", [])
    chinese_markers: list[str] = data.get("chinese", [])
    bare_words: list[str] = data.get("bare_words", [])

    # --- 裸词集合 ---
    _bare_words_set = {w.lower() for w in bare_words}

    # --- 英文标志词预编译 ---
    # markers.json 中已按长度降序排列，直接保持顺序
    _english_patterns = []
    for marker in english_markers:
        # 转义正则特殊字符，使用 \b 词边界，大小写不敏感
        pattern = re.compile(
            r"\b" + re.escape(marker) + r"\b",
            re.IGNORECASE,
        )
        is_bare = marker.lower() in _bare_words_set
        _english_patterns.append((pattern, marker, is_bare))

    # --- 裸词追加到英文标志词列表末尾（优先级最低） ---
    # 裸词不在 english 列表中，需要单独编译并追加
    english_lower_set = {m.lower() for m in english_markers}
    for word in bare_words:
        if word.lower() not in english_lower_set:
            pattern = re.compile(
                r"\b" + re.escape(word) + r"\b",
                re.IGNORECASE,
            )
            _english_patterns.append((pattern, word, True))

    # --- 中文标志词预编译 ---
    # 中文没有 \b 词边界，直接匹配即可
    # markers.json 中已按长度降序排列
    _chinese_patterns = []
    for marker in chinese_markers:
        pattern = re.compile(re.escape(marker))
        _chinese_patterns.append((pattern, marker))

    _loaded = True
    logger.info(
        "标志词加载完成: 英文 %d 条, 中文 %d 条, 裸词 %d 条",
        len(_english_patterns),
        len(_chinese_patterns),
        len(_bare_words_set),
    )


def _ensure_loaded() -> None:
    """确保标志词已加载（懒加载）。"""
    if not _loaded:
        load_markers()


# ---------------------------------------------------------------------------
# 位置区间重叠检测
# ---------------------------------------------------------------------------

def _is_overlapping(start: int, end: int, occupied: list[tuple[int, int]]) -> bool:
    """检查 [start, end) 是否与已占用区间列表有重叠。

    Args:
        start: 新匹配的起始位置。
        end: 新匹配的结束位置。
        occupied: 已占用的区间列表 [(s, e), ...]。

    Returns:
        True 表示有重叠，应跳过此匹配。
    """
    for occ_start, occ_end in occupied:
        # 两个区间 [s1, e1) 和 [s2, e2) 重叠的条件：s1 < e2 and s2 < e1
        if start < occ_end and occ_start < end:
            return True
    return False


# ---------------------------------------------------------------------------
# 主匹配函数
# ---------------------------------------------------------------------------

def _normalize_ligatures(text: str) -> str:
    """将 PDF 常见连字符替换为普通字母组合。

    PDF 字体渲染常将 fi/fl/ff/ffi/ffl 编码为单个 Unicode 连字符，
    导致正则无法匹配普通英文单词（如 ﬁrst → first）。
    """
    return (text
        .replace('\ufb00', 'ff')   # ﬀ
        .replace('\ufb01', 'fi')   # ﬁ
        .replace('\ufb02', 'fl')   # ﬂ
        .replace('\ufb03', 'ffi')  # ﬃ
        .replace('\ufb04', 'ffl')  # ﬄ
        .replace('\ufb05', 'st')   # ﬅ
        .replace('\ufb06', 'st')   # ﬆ
    )


def match_markers(text: str) -> list[MarkerMatch]:
    """在文本中匹配所有标志词，返回匹配结果列表。

    匹配规则：
    1. 长标志词优先匹配（已按长度降序排列）
    2. 已被长标志词覆盖的位置范围，短标志词不再重复匹配
    3. 裸词（bare_words）标记 is_bare_word=True

    Args:
        text: 待匹配的文本（通常为一个句子）。

    Returns:
        按 start 位置升序排列的 MarkerMatch 列表。
    """
    _ensure_loaded()
    text = _normalize_ligatures(text)

    # 已占用的区间列表
    occupied: list[tuple[int, int]] = []
    results: list[MarkerMatch] = []

    # --- 英文标志词匹配（长优先） ---
    for pattern, marker, is_bare in _english_patterns:
        for m in pattern.finditer(text):
            s, e = m.start(), m.end()
            if _is_overlapping(s, e, occupied):
                continue
            results.append(MarkerMatch(
                marker=m.group(),  # 保留原文大小写
                start=s,
                end=e,
                is_bare_word=is_bare,
            ))
            occupied.append((s, e))

    # --- 中文标志词匹配（长优先） ---
    for pattern, marker in _chinese_patterns:
        for m in pattern.finditer(text):
            s, e = m.start(), m.end()
            if _is_overlapping(s, e, occupied):
                continue
            results.append(MarkerMatch(
                marker=m.group(),
                start=s,
                end=e,
                is_bare_word=False,  # 中文标志词没有裸词概念
            ))
            occupied.append((s, e))

    # 按位置升序排列
    results.sort(key=lambda x: x.start)

    if results:
        logger.debug(
            "文本匹配到 %d 个标志词: %s",
            len(results),
            [(r.marker, r.start, r.end) for r in results],
        )

    return results
