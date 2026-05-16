"""机构查询模块

职责：
- 通过 CrossRef API 查询被评文献作者的机构和国家
- 支持 DOI 精确查询和标题搜索两种方式
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


def _title_similarity(title1: str, title2: str) -> float:
    """计算两个标题的简单相似度（基于词集合的 Jaccard 系数）

    返回 0.0~1.0，越高越相似
    """
    if not title1 or not title2:
        return 0.0

    # 归一化：小写、去标点
    def normalize(t):
        t = t.lower()
        t = re.sub(r'[^\w\s]', ' ', t)
        return set(t.split())

    set1 = normalize(title1)
    set2 = normalize(title2)

    if not set1 or not set2:
        return 0.0

    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union)


def lookup_by_doi(doi: str) -> dict:
    """通过 DOI 精确查询论文的作者机构信息

    Args:
        doi: DOI 标识符（如 "10.1016/j.xxx.2020.001"）

    Returns:
        dict with keys: institution, country, doi
    """
    result = {"institution": "", "country": "", "doi": doi}

    if not doi:
        return result

    try:
        url = f"{CROSSREF_API}/{doi}"
        params = {"mailto": MAILTO}

        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        item = data.get("message", {})
        if not item:
            logger.info(f"CrossRef DOI 查询无结果: {doi}")
            return result

        # 提取第一作者机构
        authors = item.get("author", [])
        if authors:
            target_author = authors[0]
            affiliations = target_author.get("affiliation", [])
            if affiliations:
                aff_name = affiliations[0].get("name", "")
                result["institution"] = aff_name
                result["country"] = _infer_country(aff_name)

    except httpx.TimeoutException:
        logger.warning(f"CrossRef DOI 查询超时: {doi}")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.info(f"CrossRef DOI 未找到: {doi}")
        else:
            logger.warning(f"CrossRef DOI 查询失败: {e}")
    except Exception as e:
        logger.warning(f"CrossRef DOI 查询失败: {e}")

    return result


def lookup_institution(
    title: str,
    first_author: str = "",
    year: str = "",
    doi: str = "",
) -> dict:
    """通过 CrossRef 查询论文的作者机构信息

    三级回退策略：
    1. 如果有 DOI，先用 DOI 精确查询
    2. 如果 DOI 查询无结果，用标题搜索（带相似度校验）

    Args:
        title: 论文标题
        first_author: 第一作者名（可选，用于校验）
        year: 年份（可选，用于过滤）
        doi: DOI 标识符（可选，优先使用）

    Returns:
        dict with keys: institution, country, doi
    """
    result = {"institution": "", "country": "", "doi": ""}

    # 策略1：DOI 精确查询
    if doi:
        doi_result = lookup_by_doi(doi)
        if doi_result.get("institution"):
            logger.info(f"DOI 查询成功: {doi} → {doi_result['institution']}")
            return doi_result
        # DOI 查到了但没有机构信息，记录 DOI
        result["doi"] = doi

    # 策略2：标题搜索
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

        # 选最匹配的条目，并做标题相似度校验
        best = items[0]
        best_title = " ".join(best.get("title", []))

        similarity = _title_similarity(title, best_title)
        if similarity < 0.3:
            logger.info(f"CrossRef 标题不匹配 (相似度={similarity:.2f}): "
                       f"查询='{title[:40]}' vs 结果='{best_title[:40]}'")
            # 不匹配时仍记录 DOI 但不取机构
            result["doi"] = result["doi"] or best.get("DOI", "")
            return result

        result["doi"] = result["doi"] or best.get("DOI", "")

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
    """从机构名称推断国家（返回中文国家名）"""
    country_keywords = {
        "中国": ["China", "中国", "Beijing", "Shanghai", "Guangzhou", "Shenzhen",
                  "Nanjing", "Wuhan", "Xi'an", "Chengdu", "Hangzhou", "Tianjin",
                  "Harbin", "Dalian", "Changsha", "Hefei", "Jinan", "Kunming",
                  "Lanzhou", "Fuzhou", "Xiamen"],
        "美国": ["USA", "United States", "U.S.A", "America", "U.S."],
        "英国": ["United Kingdom", "UK", "England", "Scotland", "Wales"],
        "日本": ["Japan", "Tokyo", "Osaka", "Kyoto"],
        "韩国": ["Korea", "Seoul"],
        "德国": ["Germany", "Deutschland"],
        "法国": ["France", "Paris"],
        "澳大利亚": ["Australia", "Sydney", "Melbourne"],
        "加拿大": ["Canada", "Toronto", "Vancouver"],
        "印度": ["India", "Mumbai", "Delhi"],
        "新加坡": ["Singapore"],
        "荷兰": ["Netherlands", "Amsterdam"],
        "瑞士": ["Switzerland", "Zurich", "ETH"],
        "意大利": ["Italy", "Rome", "Milan"],
        "西班牙": ["Spain", "Madrid", "Barcelona"],
        "巴西": ["Brazil"],
        "俄罗斯": ["Russia", "Moscow"],
        "新西兰": ["New Zealand"],
        "瑞典": ["Sweden", "Stockholm"],
        "以色列": ["Israel"],
        "丹麦": ["Denmark"],
        "挪威": ["Norway"],
        "芬兰": ["Finland"],
        "奥地利": ["Austria", "Vienna"],
        "比利时": ["Belgium"],
        "波兰": ["Poland"],
        "葡萄牙": ["Portugal"],
        "爱尔兰": ["Ireland"],
        "沙特阿拉伯": ["Saudi Arabia"],
        "伊朗": ["Iran"],
        "土耳其": ["Turkey", "Türkiye"],
        "泰国": ["Thailand"],
        "马来西亚": ["Malaysia"],
        "越南": ["Vietnam"],
        "巴基斯坦": ["Pakistan"],
        "埃及": ["Egypt"],
        "南非": ["South Africa"],
        "墨西哥": ["Mexico"],
        "阿根廷": ["Argentina"],
        "智利": ["Chile"],
        "捷克": ["Czech"],
        "匈牙利": ["Hungary"],
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
            # 查找最后部分是否对应已知国家
            for country, keywords in country_keywords.items():
                for kw in keywords:
                    if kw.upper() in last_part.upper():
                        return country
            return last_part

    return ""


def batch_lookup(
    papers: list[dict],
    progress_callback=None,
) -> list[dict]:
    """批量查询多篇文献的机构信息

    Args:
        papers: [{"title": ..., "first_author": ..., "year": ..., "doi": ...}, ...]
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
            doi=paper.get("doi", ""),
        )
        results.append(info)

    return results
