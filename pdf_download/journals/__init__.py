"""期刊 fetcher 模組。

架構：
- registry.py 定義 11 本期刊的 JournalConfig
- pubmed.py   實作統一的 PubMedFetcher（所有期刊共用）
- base.py     Article / IssueInfo dataclass
"""

from typing import Optional

from pdf_download.journals.base import Article, IssueInfo, JournalFetcher
from pdf_download.journals.pubmed import PubMedFetcher
from pdf_download.journals.registry import JOURNALS, JournalConfig


def get_fetcher(slug: str, http_config: Optional[dict] = None) -> PubMedFetcher:
    """取得指定期刊的 fetcher。"""
    if slug not in JOURNALS:
        raise ValueError(
            f"Unknown journal slug: {slug}. Available: {sorted(JOURNALS)}"
        )
    return PubMedFetcher(JOURNALS[slug], http_config=http_config)


def list_journals() -> list[tuple[str, str]]:
    """回傳 [(slug, full_name), ...]。"""
    return [(cfg.slug, cfg.full_name) for cfg in JOURNALS.values()]


__all__ = [
    "Article",
    "IssueInfo",
    "JournalFetcher",
    "JournalConfig",
    "PubMedFetcher",
    "JOURNALS",
    "get_fetcher",
    "list_journals",
]
