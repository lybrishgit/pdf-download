"""把 IssueInfo 渲染成 .md / .html / .json sidecar。

.json sidecar 是給 organize 指令用的：
- DOI → article metadata 的索引
- 不需要重新解析 markdown 也能查到任何 DOI 的 metadata
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from pdf_download.journals.base import IssueInfo

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    return env


class Renderer:
    def __init__(self):
        self.env = _make_env()

    def render_issue(
        self,
        issue: IssueInfo,
        fetched_at: str,
        out_dir: Path,
    ) -> tuple[Path, Path, Path]:
        """渲染單期成 .md + .html + .json，存到 out_dir。回傳三個檔案路徑。"""
        out_dir.mkdir(parents=True, exist_ok=True)

        md_path = out_dir / f"{issue.filename_stem}.md"
        html_path = out_dir / f"{issue.filename_stem}.html"
        json_path = out_dir / f"{issue.filename_stem}.json"

        md_template = self.env.get_template("issue.md.j2")
        html_template = self.env.get_template("issue.html.j2")

        md_path.write_text(
            md_template.render(issue=issue, fetched_at=fetched_at),
            encoding="utf-8",
        )
        html_path.write_text(
            html_template.render(issue=issue, fetched_at=fetched_at),
            encoding="utf-8",
        )

        # .json sidecar：給 organize 用，不渲染給人看
        json_data = {
            "journal_slug": issue.journal_slug,
            "journal_full": issue.journal_full,
            "journal_abbrev": issue.journal_abbrev,
            "volume": issue.volume,
            "issue": issue.issue,
            "publication_date": issue.publication_date,
            "fetched_at": fetched_at,
            "issue_url": issue.issue_url,
            "articles": [
                {
                    "doi": a.doi,
                    "pmid": a.pmid,
                    "title": a.title,
                    "authors": a.authors,
                    "article_type": a.article_type,
                    "pages": a.pages,
                    "is_open_access": a.is_open_access,
                }
                for a in issue.articles
            ],
        }
        json_path.write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return md_path, html_path, json_path

    def render_index(
        self,
        issues: List[IssueInfo],
        fetched_at: str,
        fetch_date: str,
        out_dir: Path,
        failed: Optional[List[dict]] = None,
    ) -> tuple[Path, Path]:
        """渲染 INDEX.md + INDEX.html。"""
        out_dir.mkdir(parents=True, exist_ok=True)
        failed = failed or []

        ctx = {
            "issues": issues,
            "fetched_at": fetched_at,
            "fetch_date": fetch_date,
            "total_articles": sum(len(i.articles) for i in issues),
            "total_oa": sum(i.oa_count for i in issues),
            "failed": failed,
        }

        md_path = out_dir / "INDEX.md"
        html_path = out_dir / "INDEX.html"

        md_path.write_text(
            self.env.get_template("index.md.j2").render(**ctx),
            encoding="utf-8",
        )
        html_path.write_text(
            self.env.get_template("index.html.j2").render(**ctx),
            encoding="utf-8",
        )
        return md_path, html_path
