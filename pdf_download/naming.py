"""PDF 命名規則。

遵循 med-literature-organizer skill 的格式：
    [年份]_[期刊縮寫]_[主題關鍵字].pdf

我們有乾淨的 metadata，所以不需要 skill 那套啟發式判斷，
只是字串組裝 + slugify 標題。
"""

from __future__ import annotations

import re
from typing import Iterable

DEFAULT_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "by",
    "from", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "being", "has", "have", "had", "do", "does", "did", "as", "that",
    "this", "these", "those",
}


def slugify_title(
    title: str,
    max_chars: int = 35,
    stopwords: Iterable[str] | None = None,
) -> str:
    """把標題壓成檔名安全的 slug。

    步驟：
      1. 移除星號（markdown 強調）等標記
      2. 拆字（保留字母數字、希臘字母、連字號）
      3. 過濾 stopwords
      4. 用 _ 連接
      5. 截斷到 max_chars（在 _ 邊界截）
    """
    sw = set(stopwords) if stopwords is not None else DEFAULT_STOPWORDS

    # 移除 italic/bold 標記與奇怪標點
    cleaned = re.sub(r"[*_`]", " ", title)
    # 統一 dash 為 -
    cleaned = re.sub(r"[‐-―]", "-", cleaned)

    # 拆字（保留 ASCII 字母數字、希臘字母、連字號）
    words = re.findall(r"[A-Za-z0-9α-ωΑ-Ω]+(?:-[A-Za-z0-9α-ωΑ-Ω]+)*", cleaned)

    # 過濾 stopwords（小寫比對，但保留原始大小寫）
    kept = [w for w in words if w.lower() not in sw]
    if not kept:  # 全被濾掉了，至少留一個
        kept = words[:1] if words else ["Untitled"]

    slug = "_".join(kept)

    # 在 _ 邊界截斷
    if len(slug) > max_chars:
        cut = slug[:max_chars]
        last_us = cut.rfind("_")
        if last_us > max_chars * 0.5:  # 至少留一半
            slug = cut[:last_us]
        else:
            slug = cut.rstrip("_-")

    return slug


def article_type_suffix(article_type: str) -> str:
    """從 article type 推 skill 規定的後綴。"""
    if not article_type:
        return ""
    t = article_type.lower()
    if "review" in t and "systematic" not in t and "meta" not in t:
        return "_Review"
    if "systematic review" in t or "meta-analysis" in t or "meta analysis" in t:
        return "_SR"
    if "guideline" in t or "recommendation" in t:
        return "_Guideline"
    if "editorial" in t:
        return "_Editorial"
    if "perspective" in t:
        return "_Perspective"
    return ""


def build_pdf_filename(
    year: str,
    journal_abbrev: str,
    title: str,
    article_type: str = "",
    max_title_chars: int = 35,
    stopwords: Iterable[str] | None = None,
) -> str:
    """組成最終 PDF 檔名。

    範例：build_pdf_filename("2026", "NEJM", "Mucosal Vaccination Clears
                              Clostridioides difficile Colonization")
         → "2026_NEJM_Mucosal_Vaccination_Clears_Clostridioides.pdf"
    """
    slug = slugify_title(title, max_chars=max_title_chars, stopwords=stopwords)
    suffix = article_type_suffix(article_type)
    return f"{year}_{journal_abbrev}_{slug}{suffix}.pdf"
