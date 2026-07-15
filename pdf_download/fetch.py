"""Fetch 主流程：跑指定期刊 → AI 評析 → 渲染 .md / .html → 更新 state。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

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
    download_oa: bool = False,
    oa_config: Optional[dict] = None,
    oa_dry_run: bool = False,
    kb_raw_dir: Optional[Path] = None,
    naming_config: Optional[dict] = None,
) -> dict:
    """跑 fetch，回傳 summary dict（給 CLI 顯示用）。

    download_oa: 抓完摘要後，順手走 OA 索引階梯把拿得到的全文下載進 _pdfs/，
                 隔天 03:00 organize 會自動改名入 KB。失敗不影響 fetch 主流程。
    """
    now = datetime.now()
    fetch_date = now.strftime("%Y-%m-%d")
    fetched_at = now.strftime("%Y-%m-%d %H:%M")

    out_dir = inbox_root / fetch_date

    issues: List[IssueInfo] = []
    rendered_html: Dict[str, Path] = {}  # issue_id → 評析頁 .html 路徑（給 email 當附件）
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
        rendered_html[issue.issue_id] = html_path
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

    # OA 全文自動下載（--download-oa）。抓進 _pdfs/ 後就交給 organize，
    # 這裡不改名、不碰 KB。整段包 try：任何失敗都不能拖垮已經產好的摘要。
    oa_results = []
    if download_oa and issues:
        try:
            from pdf_download.oa_fetch import download_oa_fulltext
            # 不用 is_open_access 當閘門：實測聯集(33) > PubMed 標 OA(24)，
            # 多出的是作者手稿/典藏版。對全部有 DOI 的篇都走一次階梯。
            candidates = [
                {"doi": a.doi.strip().lower(), "pmid": a.pmid or "",
                 "journal": i.journal_abbrev, "title": a.title,
                 "article_type": a.article_type,
                 "year": i.publication_date[:4] if i.publication_date else ""}
                for i in issues for a in i.articles if a.doi
            ]
            oa_results = download_oa_fulltext(
                candidates, pdfs_dir, oa_config or {}, dry_run=oa_dry_run,
                kb_raw_dir=kb_raw_dir, naming_config=naming_config,
            )
        except Exception as e:
            logger.error(f"OA 全文下載失敗（不影響摘要）：{e}", exc_info=True)

    state.save()

    return {
        "out_dir": out_dir,
        "oa": [{
            "doi": r.doi, "journal": r.journal, "title": r.title,
            "source": r.source, "ok": r.ok,
            "path": str(r.path) if r.path else "",
            "reason": r.reason,
        } for r in oa_results],
        "fetched": [{
            "journal": i.journal_abbrev,
            "publication_date": i.publication_date,
            "article_count": len(i.articles),
            "oa_count": i.oa_count,
            # 評析頁 .html 路徑（email 當附件帶上，點開就是完整評析版面）
            "html_path": str(rendered_html.get(i.issue_id, "")),
            # 全部文章（給 email 內文列出，OA 可直接點原文）；必讀數由 action 推算
            "articles": [
                {
                    "title": a.title,
                    "url": a.article_url or (f"https://doi.org/{a.doi}" if a.doi else ""),
                    "is_oa": a.is_open_access,
                    "stars": a.ai_analysis.stars if a.ai_analysis else 0,
                    "action": a.ai_analysis.action if a.ai_analysis else "",
                }
                for a in i.articles
            ],
        } for i in issues],
        "skipped": skipped,
        "failed": failed,
    }
