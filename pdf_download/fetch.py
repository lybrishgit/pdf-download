"""Fetch 主流程：跑指定期刊 → AI 評析 → 渲染 .md / .html → 更新 state。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pdf_download.analyzer import AbstractAnalyzer, load_prompt_template
from pdf_download.journals import JOURNALS, get_fetcher
from pdf_download.journals.base import IssueInfo
from pdf_download.render import Renderer
from pdf_download.state import State

logger = logging.getLogger(__name__)


def run_fetch(
    journal_slugs: List[str],
    inbox_root: Path,
    state: State,
    http_config: Optional[dict] = None,
    force: bool = False,
    analyzer: Optional[AbstractAnalyzer] = None,
    force_reanalyze: bool = False,
) -> dict:
    """跑 fetch，回傳 summary dict（給 CLI 顯示用）。"""
    now = datetime.now()
    fetch_date = now.strftime("%Y-%m-%d")
    fetched_at = now.strftime("%Y-%m-%d %H:%M")

    out_dir = inbox_root / fetch_date

    issues: List[IssueInfo] = []
    failed: List[dict] = []
    skipped: List[dict] = []

    for slug in journal_slugs:
        if slug not in JOURNALS:
            failed.append({"journal": slug, "reason": f"未知的期刊代號"})
            continue

        try:
            logger.info(f"=== Fetching {slug} ===")
            fetcher = get_fetcher(slug, http_config=http_config)
            issue = fetcher.fetch_current_issue()
        except Exception as e:
            logger.error(f"Failed to fetch {slug}: {e}", exc_info=True)
            failed.append({"journal": slug, "reason": str(e)})
            continue

        if not force and state.is_already_fetched(slug, issue.issue_id):
            logger.info(f"{slug} 已抓過 {issue.issue_id}，跳過。")
            skipped.append({
                "journal": slug,
                "issue_id": issue.issue_id,
                "publication_date": issue.publication_date,
            })
            continue

        if not issue.articles:
            failed.append({"journal": slug, "reason": "過濾掉所有文章（可能整期都是 Editorial/Letter/Perspective）"})
            continue

        # AI 評析（每篇 abstract 跑一次 Sonnet 4.6）
        if analyzer is not None:
            logger.info(f"AI 評析 {slug} ({len(issue.articles)} 篇)...")
            stats = analyzer.analyze_articles(
                issue.articles,
                journal_full=issue.journal_full,
                force_reanalyze=force_reanalyze,
            )
            logger.info(
                f"  cache: {stats['cached']}, fresh: {stats['fresh']}, failed: {stats['failed']}"
            )

        renderer = Renderer()
        md_path, html_path, json_path = renderer.render_issue(issue, fetched_at, out_dir)
        logger.info(f"{slug} → {md_path.name} ({len(issue.articles)} 篇)")

        issues.append(issue)
        state.record_fetch(slug, issue.issue_id, issue.publication_date)

    if issues:
        renderer = Renderer()
        renderer.render_index(issues, fetched_at, fetch_date, out_dir, failed=failed)

    # 確保 inbox 根目錄有固定的 _pdfs/ 資料夾
    # 這個位置不隨日期變動，醫院 PC 可以一次設定瀏覽器預設下載位置永久使用
    pdfs_dir = inbox_root / "_pdfs"
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    readme = pdfs_dir / "README.txt"
    if not readme.exists():
        readme.write_text(
            "把下載的 PDF 丟進這個資料夾（固定路徑，每週 fetch 不會改變）。\n"
            "之後跑：python -m pdf_download.cli organize\n"
            "工具會比對所有最近 fetch 過的 abstracts，自動改名並搬到 KB 的 00-Raw/。\n",
            encoding="utf-8",
        )

    state.save()

    return {
        "out_dir": out_dir,
        "fetched": [{
            "journal": i.journal_abbrev,
            "publication_date": i.publication_date,
            "article_count": len(i.articles),
            "oa_count": i.oa_count,
        } for i in issues],
        "skipped": skipped,
        "failed": failed,
    }
