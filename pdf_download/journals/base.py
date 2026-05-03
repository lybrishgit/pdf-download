"""期刊 fetcher 共用介面與資料結構。"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class AIAnalysis:
    """LLM 對單篇文章的評析。"""
    stars: int            # 1-5
    action: str           # "必讀" / "瀏覽" / "跳過"
    body: str             # 100-150 字評析
    model: str            # 用了哪個 model
    analyzed_at: str      # ISO timestamp

    @property
    def stars_emoji(self) -> str:
        return "⭐" * max(1, min(5, self.stars))

    @property
    def callout_class(self) -> str:
        """HTML/Obsidian callout 顏色：必讀=紅 / 瀏覽=黃 / 跳過=灰。"""
        return {"必讀": "must-read", "瀏覽": "skim", "跳過": "skip"}.get(self.action, "skim")


@dataclass
class Article:
    """單篇文章的完整資料（abstract 渲染需要的所有欄位）。"""

    title: str
    authors: str  # "Smith J, Lee MK, et al." 已格式化
    doi: str
    article_type: str  # "Original Article" / "Review" / "Editorial" / ...
    pdf_url: str
    article_url: str
    abstract_sections: List[tuple] = field(default_factory=list)
    # abstract_sections: [("Background", "..."), ("Methods", "..."), ...]
    # 沒有結構化分節時，用 [("", "整段 abstract 文字")]

    pages: Optional[str] = None  # "1567–1578"
    is_open_access: bool = False
    pmid: Optional[str] = None
    ai_analysis: Optional[AIAnalysis] = None  # 由 analyzer.py 補上


@dataclass
class IssueInfo:
    """單期期刊的 metadata。"""

    journal_slug: str       # "nejm"
    journal_full: str       # "New England Journal of Medicine"
    journal_abbrev: str     # "NEJM"（用在檔名與 PDF 命名）
    volume: str             # "392"
    issue: str              # "18"
    publication_date: str   # "2026-04-30"
    issue_url: str          # TOC 網址
    articles: List[Article] = field(default_factory=list)

    @property
    def issue_id(self) -> str:
        """用於 state file 比對是否已抓過。"""
        return f"{self.journal_slug}:vol{self.volume}:iss{self.issue}"

    @property
    def filename_stem(self) -> str:
        """輸出檔名 stem，例如 'NEJM-2026-04-30'。"""
        return f"{self.journal_abbrev}-{self.publication_date}"

    @property
    def oa_count(self) -> int:
        return sum(1 for a in self.articles if a.is_open_access)


class JournalFetcher:
    """期刊 fetcher 基底。每個期刊繼承並實作 fetch_current_issue()。"""

    slug: str = ""           # "nejm"
    full_name: str = ""      # "New England Journal of Medicine"
    abbrev: str = ""         # "NEJM"

    def __init__(self, http_config: Optional[dict] = None):
        self.http_config = http_config or {}
        self.session = requests.Session()
        ua = self.http_config.get(
            "user_agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        )
        self.session.headers.update({
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8",
        })
        self.timeout = self.http_config.get("timeout", 30)
        self.rate_limit = self.http_config.get("rate_limit_seconds", 1.5)

    def fetch_current_issue(self) -> IssueInfo:
        """抓最新一期。子類別實作。"""
        raise NotImplementedError

    def _get(self, url: str, **kwargs) -> requests.Response:
        """包裝 GET 請求，加上 rate limit 與錯誤紀錄。"""
        logger.debug(f"GET {url}")
        resp = self.session.get(url, timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        time.sleep(self.rate_limit)
        return resp
