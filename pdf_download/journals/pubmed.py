"""統一 PubMed E-utilities fetcher。

策略：
1. esearch by 期刊 + 日期窗口 → PMID 清單
2. efetch by PMIDs → XML metadata
3. 解析 XML → Article 物件
4. 按 Volume+Issue 分群，取最新一期

API 文件：
- https://www.ncbi.nlm.nih.gov/books/NBK25501/
- 不需 API key 也可用，但 rate limit 較嚴（3 req/sec）
- 加 NCBI_API_KEY 環境變數可放寬到 10 req/sec

PubMed 索引延遲：
- 通常 1–3 天，少數情況一週左右
- 配合使用者每週節奏可接受
"""

from __future__ import annotations

import logging
import os
import re
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

import requests

from pdf_download.journals.base import Article, IssueInfo
from pdf_download.journals.registry import JournalConfig

logger = logging.getLogger(__name__)


EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# 搜尋窗口大一點容易找到完整的「最新一期」
# 重點不是窗口本身，而是後續的 issue 分群選擇邏輯
# 90 天涵蓋月刊一期、雙週刊兩期，足夠了
CADENCE_LOOKBACK_DAYS = {
    "weekly": 60,
    "biweekly": 90,
    "monthly": 120,
}

# 一期至少要有幾篇，才視為「已完整索引」
# 低於這個門檻可能是 PubMed 還在補索引最新期
MIN_ARTICLES_FOR_COMPLETE_ISSUE = 3


# ===== Article type 過濾 =====
# 使用者要的：original article / case study / clinical practice / review
# 排除：perspective / correspondence / editorial / news / 等

# PubMed PublicationType 直接 ALLOW 的（任何一個 match 就收）
PUBTYPE_ALLOW = {
    "Randomized Controlled Trial",
    "Clinical Trial",
    "Pragmatic Clinical Trial",
    "Adaptive Clinical Trial",
    "Clinical Trial, Phase I",
    "Clinical Trial, Phase II",
    "Clinical Trial, Phase III",
    "Clinical Trial, Phase IV",
    "Controlled Clinical Trial",
    "Equivalence Trial",
    "Multicenter Study",
    "Observational Study",
    "Comparative Study",
    "Validation Study",
    "Evaluation Study",
    "Meta-Analysis",
    "Systematic Review",
    "Review",
    "Practice Guideline",
    "Guideline",
    "Consensus Development Conference",
    "Case Reports",
}

# PubMed PublicationType 直接 DENY 的（任何一個 match 就排除）
PUBTYPE_DENY = {
    "Editorial",
    "Comment",
    "Letter",
    "News",
    "Newspaper Article",
    "Personal Narrative",
    "Biography",
    "Interview",
    "Portrait",
    "Historical Article",
    "Published Erratum",
    "Retraction of Publication",
    "Retracted Publication",
    "Webcast",
    "Video-Audio Media",
}

# NEJM DOI 模式級排除（PubMed PublicationType 沒標清楚時補強）
# 用 regex 精確比對，避免 NEJMcp / NEJMcpc 被 NEJMc 前綴誤殺
#
# NEJM 前綴對照：
#   NEJMoa  = Original Article         ← 收
#   NEJMra  = Review Article           ← 收
#   NEJMcp  = Clinical Practice        ← 收（使用者要的）
#   NEJMcpc = Case Records (MGH)       ← 收（case study）
#   NEJMcps = Clinical Problem-Solving ← 收（臨床推理）
#   NEJMc<digit>  = Correspondence/Letter  ← 排除
#   NEJMp<digit>  = Perspective            ← 排除
#   NEJMe<digit>  = Editorial              ← 排除
#   NEJMicm = Images in Clinical Medicine  ← 排除（純影像，非典型案例）
NEJM_DENY_PATTERNS = [
    re.compile(r"^10\.1056/NEJMp\d"),     # Perspective
    re.compile(r"^10\.1056/NEJMc\d"),     # Correspondence (c 後接數字才算)
    re.compile(r"^10\.1056/NEJMe\d"),     # Editorial
    re.compile(r"^10\.1056/NEJMicm"),     # Images in Clinical Medicine
]


def should_include_article(pub_types: set, doi: str, has_abstract: bool) -> bool:
    """根據 PubMed PublicationType 與 DOI 判斷是否該收。

    規則：
    1. NEJM 特定前綴（NEJMp/NEJMc/NEJMe 等）→ 直接排除
    2. PublicationType 含 DENY 集合任何一個 → 排除
    3. PublicationType 含 ALLOW 集合任何一個 → 收
    4. 只剩 'Journal Article'（PubMed 對普通研究的 fallback）→
       有 abstract 視為原始研究，收；無 abstract 多半是 Perspective/Letter，排除
    """
    # NEJM-specific 排除
    for pat in NEJM_DENY_PATTERNS:
        if pat.match(doi):
            return False

    if pub_types & PUBTYPE_DENY:
        return False
    if pub_types & PUBTYPE_ALLOW:
        return True

    # 邊界情況：只有 'Journal Article'。靠 abstract 存在判斷
    return has_abstract


class PubMedFetcher:
    """以 PubMed 為單一資料源的期刊 fetcher。"""

    def __init__(self, config: JournalConfig, http_config: Optional[dict] = None):
        self.config = config
        self.http_config = http_config or {}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.http_config.get(
                "user_agent",
                "pdf-download/0.1 (https://github.com/lybrish; mailto:lybrish@gmail.com)",
            ),
        })
        self.timeout = self.http_config.get("timeout", 30)
        self.api_key = os.environ.get("NCBI_API_KEY")  # 可選

    # ---------- 主流程 ----------

    def fetch_current_issue(self) -> IssueInfo:
        pmids = self._search_recent_pmids()
        if not pmids:
            raise RuntimeError(
                f"PubMed 搜尋 {self.config.iso_abbrev} 在過去 "
                f"{CADENCE_LOOKBACK_DAYS[self.config.cadence]} 天內沒有結果。"
            )

        articles_by_issue = self._fetch_and_group(pmids)
        if not articles_by_issue:
            raise RuntimeError(f"{self.config.abbrev} 抓到 PMID 但解析後無文章")

        # 選「目前最值得看」的那一期：
        # - 優先：最新且文章數 >= 門檻的那期（避免抓到 PubMed 還在索引中的當週新期）
        # - 退而求其次：所有期裡文章最多的那期
        # 這個策略同時處理「PubMed 索引延遲」和「期刊出刊間隔長」兩種情況
        sorted_issues = sorted(
            articles_by_issue.keys(),
            key=lambda k: (k[2], self._safe_int(k[0]), self._safe_int(k[1])),
            reverse=True,
        )
        latest_key = None
        for k in sorted_issues:
            if len(articles_by_issue[k]) >= MIN_ARTICLES_FOR_COMPLETE_ISSUE:
                latest_key = k
                break
        if latest_key is None:
            # 沒有任何一期達到門檻，挑文章最多的
            latest_key = max(articles_by_issue.keys(),
                             key=lambda k: len(articles_by_issue[k]))

        volume, issue, pub_date = latest_key
        articles = articles_by_issue[latest_key]

        return IssueInfo(
            journal_slug=self.config.slug,
            journal_full=self.config.full_name,
            journal_abbrev=self.config.abbrev,
            volume=volume or "",
            issue=issue or "",
            publication_date=pub_date,
            issue_url=f"https://pubmed.ncbi.nlm.nih.gov/?term="
                      f"{urllib.parse.quote(self.config.iso_abbrev)}%5BJournal%5D"
                      f"+AND+{volume}%5BVolume%5D+AND+{issue}%5BIssue%5D",
            articles=articles,
        )

    # ---------- esearch ----------

    def _search_recent_pmids(self) -> List[str]:
        days = CADENCE_LOOKBACK_DAYS.get(self.config.cadence, 30)
        end = datetime.now()
        start = end - timedelta(days=days)

        # PubMed Date 範圍正確語法：
        #   "YYYY/MM/DD"[PDAT] : "YYYY/MM/DD"[PDAT]
        # 兩個日期各自用引號包住，並各自接 [PDAT]，中間用冒號隔開
        start_s = start.strftime("%Y/%m/%d")
        end_s = end.strftime("%Y/%m/%d")
        term = (
            f'"{self.config.iso_abbrev}"[Journal] AND '
            f'("{start_s}"[PDAT] : "{end_s}"[PDAT])'
        )

        params = {
            "db": "pubmed",
            "term": term,
            "retmax": 200,
            "retmode": "json",
            "sort": "pub_date",
        }
        if self.api_key:
            params["api_key"] = self.api_key

        url = f"{EUTILS_BASE}/esearch.fcgi"
        logger.debug(f"esearch: {term}")
        resp = self._get(url, params=params)
        data = resp.json()
        ids = data.get("esearchresult", {}).get("idlist", [])
        logger.info(f"{self.config.abbrev}: 找到 {len(ids)} 個 PMID 在過去 {days} 天")
        return ids

    # ---------- efetch ----------

    def _fetch_and_group(self, pmids: List[str]) -> Dict[Tuple[str, str, str], List[Article]]:
        """efetch 全部 PMID，解析後按 (Vol, Iss, PubDate) 分群。"""
        if not pmids:
            return {}

        # PubMed 建議每次 efetch ≤200 個 ID
        articles: List[Article] = []
        publish_dates: Dict[str, str] = {}  # PMID → date

        for chunk_start in range(0, len(pmids), 200):
            chunk = pmids[chunk_start:chunk_start + 200]
            params = {
                "db": "pubmed",
                "id": ",".join(chunk),
                "rettype": "abstract",
                "retmode": "xml",
            }
            if self.api_key:
                params["api_key"] = self.api_key

            url = f"{EUTILS_BASE}/efetch.fcgi"
            logger.debug(f"efetch chunk: {len(chunk)} PMIDs")
            resp = self._get(url, params=params)
            root = ET.fromstring(resp.content)

            for art_elem in root.findall(".//PubmedArticle"):
                article, vol, iss, pub_date = self._parse_article(art_elem)
                if article is None:
                    continue
                articles.append((vol, iss, pub_date, article))

        # 分群
        grouped: Dict[Tuple[str, str, str], List[Article]] = defaultdict(list)
        for vol, iss, pub_date, art in articles:
            grouped[(vol, iss, pub_date)].append(art)

        # 排序文章：按頁碼或 PMID
        for key in grouped:
            grouped[key].sort(key=lambda a: (
                self._page_sort_key(a.pages),
                a.pmid or "",
            ))

        return dict(grouped)

    @staticmethod
    def _page_sort_key(pages: Optional[str]) -> int:
        """從頁碼字串提第一個數字當排序 key。"""
        if not pages:
            return 999999
        m = re.match(r"\s*(\d+)", pages)
        return int(m.group(1)) if m else 999999

    @staticmethod
    def _safe_int(s: Optional[str]) -> int:
        """volume/issue 可能含字母（如 'Suppl 1'），抽出第一個數字。"""
        if not s:
            return 0
        m = re.search(r"\d+", str(s))
        return int(m.group(0)) if m else 0

    # ---------- XML 解析 ----------

    def _parse_article(self, art_elem) -> Tuple[Optional[Article], str, str, str]:
        """從 <PubmedArticle> 元素抽出資料。回傳 (Article, volume, issue, pub_date)。"""
        # PMID
        pmid_el = art_elem.find(".//PMID")
        pmid = pmid_el.text if pmid_el is not None else ""

        # Title（PubMed 標題常以句點結尾，去掉看起來比較乾淨）
        title_el = art_elem.find(".//ArticleTitle")
        title = self._clean_text(title_el) if title_el is not None else ""
        if not title:
            return None, "", "", ""
        title = title.rstrip(".").rstrip()

        # Volume / Issue
        vol_el = art_elem.find(".//JournalIssue/Volume")
        iss_el = art_elem.find(".//JournalIssue/Issue")
        volume = vol_el.text if vol_el is not None else ""
        issue = iss_el.text if iss_el is not None else ""

        # Publication date
        pub_date = self._extract_pub_date(art_elem)

        # DOI / PII
        doi = ""
        pii = ""
        for eid in art_elem.findall(".//ELocationID") + art_elem.findall(".//ArticleId"):
            id_type = (eid.get("EIdType") or eid.get("IdType") or "").lower()
            value = (eid.text or "").strip()
            if id_type == "doi" and not doi:
                # 過濾 supplementary 的 DOI（含 #、@ 等）
                if not re.search(r"[#@]", value):
                    doi = value
            elif id_type == "pii" and not pii:
                pii = value

        if not doi:
            return None, "", "", ""  # 沒 DOI 沒辦法構 PDF URL，跳過

        # Authors
        authors = self._format_authors(art_elem)

        # Article type + 收件過濾
        all_pub_types = {pt.text for pt in art_elem.findall(".//PublicationType") if pt.text}
        article_type = self._primary_pub_type_from_set(all_pub_types)
        # 抽 abstract（filter 邏輯需要知道有無）
        abstract_sections = self._extract_abstract_sections(art_elem)
        if not should_include_article(all_pub_types, doi, has_abstract=bool(abstract_sections)):
            return None, "", "", ""

        # Pages
        pages_el = art_elem.find(".//Pagination/MedlinePgn")
        pages = pages_el.text if pages_el is not None and pages_el.text else ""
        first_page = ""
        if pages:
            m = re.match(r"\s*(\d+)", pages)
            if m:
                first_page = m.group(1)

        # OA detection（簡易版：有 PMC ID 就認定是 OA 或可在 PMC 看到）
        is_oa = False
        for aid in art_elem.findall(".//ArticleId"):
            if (aid.get("IdType") or "").lower() == "pmc":
                is_oa = True
                break

        # 組 PDF URL
        try:
            pdf_url = self.config.pdf_url.format(
                doi=doi, pii=pii, volume=volume, issue=issue, first_page=first_page,
            )
        except KeyError:
            pdf_url = f"https://doi.org/{doi}"
        try:
            article_url = self.config.article_url.format(
                doi=doi, pii=pii, volume=volume, issue=issue, first_page=first_page,
            )
        except KeyError:
            article_url = f"https://doi.org/{doi}"

        article = Article(
            title=title,
            authors=authors,
            doi=doi,
            article_type=article_type,
            pdf_url=pdf_url,
            article_url=article_url,
            abstract_sections=abstract_sections,
            pages=pages,
            is_open_access=is_oa,
            pmid=pmid,
        )
        return article, volume, issue, pub_date

    def _extract_pub_date(self, art_elem) -> str:
        """從 JournalIssue/PubDate 抽出 YYYY-MM-DD 格式。"""
        pd = art_elem.find(".//JournalIssue/PubDate")
        if pd is None:
            return ""

        year = (pd.findtext("Year") or "").strip()
        month = (pd.findtext("Month") or "").strip()
        day = (pd.findtext("Day") or "").strip()

        # MedlineDate fallback (e.g. "2026 Spring")
        if not year:
            md = pd.findtext("MedlineDate")
            if md:
                m = re.search(r"\b(\d{4})\b", md)
                if m:
                    year = m.group(1)

        if not year:
            return ""

        # Month 可能是 "Jan" / "1" / "January"
        month_num = self._month_to_num(month) if month else "01"
        day_num = day.zfill(2) if day else "01"

        try:
            return f"{year}-{month_num}-{day_num}"
        except Exception:
            return year

    @staticmethod
    def _month_to_num(month: str) -> str:
        if month.isdigit():
            return month.zfill(2)
        try:
            dt = datetime.strptime(month[:3], "%b")
            return f"{dt.month:02d}"
        except ValueError:
            try:
                dt = datetime.strptime(month, "%B")
                return f"{dt.month:02d}"
            except ValueError:
                return "01"

    def _format_authors(self, art_elem) -> str:
        """產生 'Smith J, Lee MK, et al.' 格式。"""
        authors = []
        for au in art_elem.findall(".//AuthorList/Author"):
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

    @staticmethod
    def _primary_pub_type_from_set(types: set) -> str:
        """從一組 PublicationType 中挑最有意義的一個。

        若只有 'Journal Article'，回傳空字串。
        """
        priority = [
            "Randomized Controlled Trial",
            "Clinical Trial",
            "Meta-Analysis",
            "Systematic Review",
            "Practice Guideline",
            "Guideline",
            "Review",
            "Observational Study",
            "Multicenter Study",
            "Case Reports",
        ]
        filtered = {t for t in types if t and t != "Journal Article"}
        if not filtered:
            return ""
        for p in priority:
            if p in filtered:
                return p
        return next(iter(filtered))

    def _extract_abstract_sections(self, art_elem) -> List[Tuple[str, str]]:
        """抽 abstract 的結構化段落。"""
        sections = []
        for at in art_elem.findall(".//Abstract/AbstractText"):
            label = at.get("Label") or at.get("NlmCategory") or ""
            text = self._clean_text(at)
            if not text:
                continue
            # Label 美化：BACKGROUND → Background
            if label:
                label = label.title()
            sections.append((label, text))
        return sections

    @staticmethod
    def _clean_text(elem) -> str:
        """把 element 內含 inline tag (i, b, sub, sup) 的文字平鋪成純文字。"""
        if elem is None:
            return ""
        # itertext 會展開所有子元素的 .text
        text = "".join(elem.itertext())
        return re.sub(r"\s+", " ", text).strip()

    # ---------- HTTP ----------

    def _get(self, url: str, **kwargs):
        last_err = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=self.timeout, **kwargs)
                resp.raise_for_status()
                # NCBI 建議無 API key 時 ≤3 req/sec
                time.sleep(0.34 if not self.api_key else 0.11)
                return resp
            except requests.RequestException as e:
                last_err = e
                logger.warning(f"PubMed request failed (attempt {attempt + 1}/3): {e}")
                time.sleep(2 ** attempt)
        raise last_err
