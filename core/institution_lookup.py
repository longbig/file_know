"""机构查询模块

职责：
- 通过 CrossRef API 查询被评文献作者的机构和国家
- 免费接口，无需 API 密钥
"""

import logging
import re

import httpx

logger = logging.getLogger(__name__)

# CrossRef API
CROSSREF_API = "https://api.crossref.org/works"
MAILTO = "academic-tool@example.com"  # 礼貌请求池


def _normalize_title(title: str) -> str:
    """规范化标题用于搜索"""
    # 去除多余空格
    title = re.sub(r'\s+', ' ', title).strip()
    return title


def lookup_institution(
    title: str,
    first_author: str = "",
    year: str = "",
) -> dict:
    """通过 CrossRef 查询论文的作者机构信息

    Args:
        title: 论文标题
        first_author: 第一作者名（可选，用于校验）
        year: 年份（可选，用于过滤）

    Returns:
        dict with keys: institution, country, doi
    """
    result = {"institution": "", "country": "", "doi": ""}

    if not title:
        return result

    try:
        params = {
            "query.title": _normalize_title(title),
            "rows": 3,
            "mailto": MAILTO,
        }
        if year:
            params["filter"] = f"from-pub-date:{year},until-pub-date:{year}"

        with httpx.Client(timeout=15.0) as client:
            resp = client.get(CROSSREF_API, params=params)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("message", {}).get("items", [])
        if not items:
            logger.info(f"CrossRef 未找到: {title[:50]}")
            return result

        # 选最匹配的条目
        best = items[0]
        result["doi"] = best.get("DOI", "")

        # 提取作者机构
        authors = best.get("author", [])
        if authors:
            # 找第一作者
            target_author = authors[0]
            if first_author:
                for a in authors:
                    family = a.get("family", "")
                    given = a.get("given", "")
                    if first_author in family or first_author in given or family in first_author:
                        target_author = a
                        break

            affiliations = target_author.get("affiliation", [])
            if affiliations:
                aff_name = affiliations[0].get("name", "")
                result["institution"] = aff_name
                # 从机构名推断国家
                result["country"] = _infer_country(aff_name)

    except httpx.TimeoutException:
        logger.warning(f"CrossRef 查询超时: {title[:50]}")
    except Exception as e:
        logger.warning(f"CrossRef 查询失败: {e}")

    return result


def _infer_country(affiliation: str) -> str:
    """从机构名称推断国家"""
    country_keywords = {
        "China": ["China", "中国", "Beijing", "Shanghai", "Guangzhou", "Shenzhen",
                   "Nanjing", "Wuhan", "Xi'an", "Chengdu", "Hangzhou"],
        "USA": ["USA", "United States", "U.S.A", "America"],
        "UK": ["United Kingdom", "UK", "England", "Scotland", "Wales"],
        "Japan": ["Japan", "Tokyo", "Osaka", "Kyoto"],
        "Korea": ["Korea", "Seoul"],
        "Germany": ["Germany", "Deutschland"],
        "France": ["France", "Paris"],
        "Australia": ["Australia", "Sydney", "Melbourne"],
        "Canada": ["Canada", "Toronto", "Vancouver"],
        "India": ["India", "Mumbai", "Delhi"],
        "Singapore": ["Singapore"],
        "Netherlands": ["Netherlands", "Amsterdam"],
        "Switzerland": ["Switzerland", "Zurich"],
        "Italy": ["Italy", "Rome", "Milan"],
        "Spain": ["Spain", "Madrid", "Barcelona"],
        "Brazil": ["Brazil"],
        "Russia": ["Russia", "Moscow"],
        "New Zealand": ["New Zealand"],
    }

    aff_upper = affiliation.upper()
    for country, keywords in country_keywords.items():
        for kw in keywords:
            if kw.upper() in aff_upper:
                return country

    # 尝试从最后的逗号分隔部分提取国家名
    parts = affiliation.split(",")
    if parts:
        last_part = parts[-1].strip()
        if len(last_part) <= 30:  # 合理的国家名长度
            return last_part

    return ""


def batch_lookup(
    papers: list[dict],
    progress_callback=None,
) -> list[dict]:
    """批量查询多篇文献的机构信息

    Args:
        papers: [{"title": ..., "first_author": ..., "year": ...}, ...]
        progress_callback: 进度回调

    Returns:
        对应的机构信息列表
    """
    results = []
    for i, paper in enumerate(papers):
        if progress_callback:
            progress_callback(f"查询机构信息 {i+1}/{len(papers)}...")

        info = lookup_institution(
            title=paper.get("title", ""),
            first_author=paper.get("first_author", ""),
            year=paper.get("year", ""),
        )
        results.append(info)

    return results
