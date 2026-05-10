"""DOI → article metadata 線上查詢（給 organize 的 fallback 用）。

當 organize 在 inbox cache 找不到 DOI 時，呼叫這個模組打 PubMed 查 metadata。
適用場景：
  - 使用者隨手丟非 11 本期刊的論文進 _pdfs/（例如 Lancet Respir Med、JTO）
  - 比較舊的論文（pdf-download 還沒 fetch 過的那期）
  - 任何 PubMed 有索引的論文都能走 organize 流程

不適用：
  - 不在 PubMed 的論文（教科書、預印本、灰文獻）
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


@dataclass
class LookupResult:
    """PubMed 查 DOI 回來的精簡 metadata。"""
    doi: str
    pmid: str
    title: str
    authors: str
    journal_iso: str         # PubMed 的 ISO abbreviation
    journal_full: str        # 完整名稱
    publication_date: str    # YYYY-MM-DD（缺日期就 YYYY-MM-01）
    article_type: str        # 第一個有意義的 PublicationType
    pages: str
    is_open_access: bool


def lookup_doi(doi: str, timeout: int = 30, rate_limit: float = 0.34) -> Optional[LookupResult]:
    """用 DOI 在 PubMed 查 metadata。失敗回 None。"""
    if not doi:
        return None
    api_key = os.environ.get("NCBI_API_KEY")
    pmid = _doi_to_pmid(doi, timeout, api_key)
    if not pmid:
        logger.debug(f"DOI {doi} 在 PubMed 查不到")
        time.sleep(rate_limit)
        return None
    time.sleep(rate_limit)
    return _efetch_one(pmid, doi, timeout, api_key, rate_limit)


def _request_with_retry(url: str, params: dict, timeout: int,
                        label: str, max_attempts: int = 3) -> Optional[requests.Response]:
    """打 NCBI E-utilities 加 retry。

    為什麼要 retry：PubMed 偶爾回 5xx 或 socket 抖動（實測過 RBTI/LWW
    第一次失敗、第二次成功的情況）。沒重試會誤判為「PubMed 也查不到」。

    策略：最多 3 次，指數退避（1 秒、2 秒）。4xx 視為真錯誤不重試。
    回傳 None 代表全部嘗試都失敗。
    """
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            # 4xx 是真錯誤（例如參數壞），不重試
            if 400 <= resp.status_code < 500:
                logger.warning(f"{label} HTTP {resp.status_code}（4xx 不重試）")
                return None
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt < max_attempts:
                backoff = 2 ** (attempt - 1)  # 1, 2, ...
                logger.info(f"{label} 第 {attempt}/{max_attempts} 次失敗（{e}），{backoff} 秒後重試")
                time.sleep(backoff)
            else:
                logger.warning(f"{label} 重試 {max_attempts} 次都失敗: {e}")
    return None


def _doi_to_pmid(doi: str, timeout: int, api_key: Optional[str]) -> Optional[str]:
    params = {
        "db": "pubmed",
        "term": f"{doi}[doi]",
        "retmode": "json",
        "retmax": 1,
    }
    if api_key:
        params["api_key"] = api_key

    resp = _request_with_retry(f"{EUTILS_BASE}/esearch.fcgi", params, timeout,
                                label=f"esearch DOI={doi}")
    if resp is None:
        return None
    try:
        data = resp.json()
        ids = data.get("esearchresult", {}).get("idlist", [])
        return ids[0] if ids else None
    except ValueError as e:
        logger.warning(f"esearch DOI={doi} 回傳非 JSON: {e}")
        return None


def _efetch_one(pmid: str, doi: str, timeout: int, api_key: Optional[str],
                rate_limit: float) -> Optional[LookupResult]:
    params = {
        "db": "pubmed",
        "id": pmid,
        "rettype": "abstract",
        "retmode": "xml",
    }
    if api_key:
        params["api_key"] = api_key

    resp = _request_with_retry(f"{EUTILS_BASE}/efetch.fcgi", params, timeout,
                                label=f"efetch PMID={pmid}")
    if resp is None:
        return None
    try:
        root = ET.fromstring(resp.content)
        time.sleep(rate_limit)
        art = root.find(".//PubmedArticle")
        if art is None:
            return None
        return _parse_article(art, doi, pmid)
    except ET.ParseError as e:
        logger.warning(f"efetch PMID={pmid} XML 解析失敗: {e}")
        return None


def _parse_article(art, doi: str, pmid: str) -> LookupResult:
    """從 <PubmedArticle> 抽 metadata。簡化版，跟 pubmed.py 部分重疊。"""
    title_el = art.find(".//ArticleTitle")
    title = _clean_text(title_el).rstrip(".").rstrip() if title_el is not None else ""

    journal_iso_el = art.find(".//ISOAbbreviation")
    journal_iso = journal_iso_el.text if journal_iso_el is not None else ""

    journal_full_el = art.find(".//Journal/Title")
    journal_full = journal_full_el.text if journal_full_el is not None else journal_iso

    pub_date = _extract_pub_date(art)

    pages_el = art.find(".//Pagination/MedlinePgn")
    pages = pages_el.text if pages_el is not None and pages_el.text else ""

    authors = _format_authors(art)
    article_type = _primary_pub_type(art)

    is_oa = False
    for aid in art.findall(".//ArticleId"):
        if (aid.get("IdType") or "").lower() == "pmc":
            is_oa = True
            break

    return LookupResult(
        doi=doi,
        pmid=pmid,
        title=title,
        authors=authors,
        journal_iso=journal_iso or journal_full,
        journal_full=journal_full or journal_iso,
        publication_date=pub_date,
        article_type=article_type,
        pages=pages,
        is_open_access=is_oa,
    )


def _clean_text(elem) -> str:
    if elem is None:
        return ""
    return re.sub(r"\s+", " ", "".join(elem.itertext())).strip()


def _extract_pub_date(art) -> str:
    pd = art.find(".//JournalIssue/PubDate")
    if pd is None:
        return ""
    year = (pd.findtext("Year") or "").strip()
    month = (pd.findtext("Month") or "").strip()
    day = (pd.findtext("Day") or "").strip()
    if not year:
        md = pd.findtext("MedlineDate") or ""
        m = re.search(r"\b(\d{4})\b", md)
        if m:
            year = m.group(1)
    if not year:
        return ""
    month_num = _month_to_num(month) if month else "01"
    day_num = day.zfill(2) if day else "01"
    return f"{year}-{month_num}-{day_num}"


def _month_to_num(month: str) -> str:
    if month.isdigit():
        return month.zfill(2)
    from datetime import datetime
    for fmt in ("%b", "%B"):
        try:
            return f"{datetime.strptime(month[:3 if fmt == '%b' else None], fmt).month:02d}"
        except ValueError:
            continue
    return "01"


def _format_authors(art) -> str:
    authors = []
    for au in art.findall(".//AuthorList/Author"):
        last = (au.findtext("LastName") or "").strip()
        init = (au.findtext("Initials") or "").strip()
        if last:
            authors.append(f"{last} {init}".strip() if init else last)
        elif au.findtext("CollectiveName"):
            authors.append(au.findtext("CollectiveName").strip())
    if not authors:
        return ""
    if len(authors) <= 3:
        return ", ".join(authors)
    return ", ".join(authors[:3]) + ", et al."


def _primary_pub_type(art) -> str:
    priority = [
        "Randomized Controlled Trial", "Clinical Trial", "Meta-Analysis",
        "Systematic Review", "Practice Guideline", "Guideline", "Review",
        "Observational Study", "Multicenter Study", "Case Reports",
    ]
    types = {pt.text for pt in art.findall(".//PublicationType")
             if pt.text and pt.text != "Journal Article"}
    if not types:
        return ""
    for p in priority:
        if p in types:
            return p
    return next(iter(types))
