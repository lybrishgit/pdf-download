"""LLM 評析模組。

對每篇 Article 呼叫 Claude API，產生 AIAnalysis（stars + action + body）。

設計重點：
- 以 PMID 為 cache key，cache 在 ~/.config/pdf-download/ai-cache.json
- prompt 內容變動時 cache 失效（用 prompt hash 比對）
- 失敗不 crash，把 ai_analysis 留 None，下次重跑會補
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import anthropic
from dotenv import load_dotenv

from pdf_download.journals.base import AIAnalysis, Article

logger = logging.getLogger(__name__)


class AnalysisCache:
    """以 PMID 為 key 的 JSON-backed cache。"""

    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, pmid: str, prompt_hash: str, model: str) -> Optional[AIAnalysis]:
        if not pmid:
            return None
        rec = self.data.get(pmid)
        if not rec:
            return None
        # prompt 改了 / model 換了 → cache 失效
        if rec.get("prompt_hash") != prompt_hash or rec.get("model") != model:
            return None
        try:
            return AIAnalysis(
                stars=rec["stars"],
                action=rec["action"],
                body=rec["body"],
                model=rec["model"],
                analyzed_at=rec["analyzed_at"],
            )
        except KeyError:
            return None

    def put(self, pmid: str, analysis: AIAnalysis, prompt_hash: str) -> None:
        if not pmid:
            return
        self.data[pmid] = {
            **asdict(analysis),
            "prompt_hash": prompt_hash,
        }


class AbstractAnalyzer:
    """以 Anthropic Claude API 評析 abstract。"""

    def __init__(
        self,
        model: str,
        prompt_template: str,
        cache_path: Path,
        api_key: Optional[str] = None,
        max_tokens: int = 600,
    ):
        # 從專案根目錄的 .env 載 API key（override=True 避免 shell 的空值蓋掉）
        repo_root = Path(__file__).resolve().parent.parent
        load_dotenv(repo_root / ".env", override=True)
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "找不到 ANTHROPIC_API_KEY。請在專案根目錄建立 .env 並寫入："
                "ANTHROPIC_API_KEY=sk-ant-... （可從 KB-Scripts/.env 複製）"
            )

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.prompt_template = prompt_template
        self.prompt_hash = hashlib.sha256(
            (prompt_template + model).encode("utf-8")
        ).hexdigest()[:16]
        self.cache = AnalysisCache(cache_path)
        self.max_tokens = max_tokens

    # ---------- 主流程 ----------

    def analyze_articles(
        self,
        articles: List[Article],
        journal_full: str,
        force_reanalyze: bool = False,
    ) -> dict:
        """對一批文章評析，直接寫入 article.ai_analysis。

        回傳 stats dict（cached / fresh / failed 各幾篇）。
        """
        cached = fresh = failed = 0
        total = len(articles)

        for i, art in enumerate(articles, 1):
            if not art.pmid:
                continue

            # cache check
            if not force_reanalyze:
                hit = self.cache.get(art.pmid, self.prompt_hash, self.model)
                if hit:
                    art.ai_analysis = hit
                    cached += 1
                    continue

            # call API
            try:
                logger.info(f"  [{i}/{total}] {art.pmid} {art.title[:60]}...")
                analysis = self._call_api(art, journal_full)
                art.ai_analysis = analysis
                self.cache.put(art.pmid, analysis, self.prompt_hash)
                fresh += 1
            except Exception as e:
                logger.warning(f"  分析失敗 {art.pmid}: {e}")
                failed += 1

        # 寫 cache（即使部分失敗也存已成功的）
        self.cache.save()

        return {"total": total, "cached": cached, "fresh": fresh, "failed": failed}

    # ---------- API 呼叫 ----------

    def _call_api(self, article: Article, journal_full: str) -> AIAnalysis:
        prompt = self._build_prompt(article, journal_full)

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else ""
        return self._parse_response(text)

    def _build_prompt(self, article: Article, journal_full: str) -> str:
        abstract_text = self._format_abstract(article.abstract_sections)
        return self.prompt_template.format(
            journal=journal_full,
            article_type=article.article_type or "Article",
            title=article.title,
            authors=article.authors,
            abstract=abstract_text or "(無 abstract — 可能是 review 或 case report 類)",
        )

    @staticmethod
    def _format_abstract(sections) -> str:
        parts = []
        for header, body in sections:
            if header:
                parts.append(f"{header}: {body}")
            else:
                parts.append(body)
        return "\n\n".join(parts)

    # ---------- 回應解析 ----------

    def _parse_response(self, text: str) -> AIAnalysis:
        """從結構化回應抽出 stars / action / body。"""
        text = text.strip()

        # STARS line
        stars_count = 4  # default fallback
        m = re.search(r"STARS\s*:\s*(\S.*)", text)
        if m:
            stars_count = m.group(1).count("⭐") or m.group(1).count("★")
            if stars_count == 0:
                # 也許用數字 "4"
                num = re.search(r"\d+", m.group(1))
                if num:
                    stars_count = int(num.group(0))

        # ACTION line
        action = "瀏覽"  # default
        m = re.search(r"ACTION\s*:\s*(\S.*)", text)
        if m:
            action_raw = m.group(1).strip()
            for token in ("必讀", "瀏覽", "跳過"):
                if token in action_raw:
                    action = token
                    break

        # ANALYSIS body — 抓 ANALYSIS: 之後到結尾
        body = ""
        m = re.search(r"ANALYSIS\s*:\s*(.+)", text, re.S)
        if m:
            body = m.group(1).strip()
            # 移除尾端可能的 ``` 或多餘空白
            body = re.sub(r"```\s*$", "", body).strip()

        if not body:
            # parse 完全失敗的話，把整段當 body
            body = text or "(評析解析失敗)"

        return AIAnalysis(
            stars=max(1, min(5, stars_count)),
            action=action,
            body=body,
            model=self.model,
            analyzed_at=datetime.now().isoformat(timespec="seconds"),
        )


def load_prompt_template(path: Path) -> str:
    return path.read_text(encoding="utf-8")
