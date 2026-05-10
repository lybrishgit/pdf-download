"""把 _pdfs/ 裡的 PDF 自動改名 + 搬到 KB 的 00-Raw/。

流程：
  1. 從 inbox_root 下所有日期資料夾的 .json sidecar 建 DOI → metadata 索引
  2. 掃 inbox_root/_pdfs/ 的 PDF
  3. 對每個 PDF：
     a. 先用檔名抽 DOI（NEJM / Springer / LWW 各家命名習慣）
     b. 失敗則用 PyPDF2 讀 PDF metadata
     c. 還失敗則掃第一頁文字找 DOI 字串
  4. 找到 DOI 就查索引，套用 naming.py 規則改名
  5. shutil.move 到 KB 00-Raw/
  6. 沒 match 到的留在 _pdfs/，全部寫進 organize-log.md
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PyPDF2 import PdfReader
from PyPDF2.errors import PdfReadError

from pdf_download.lookup import LookupResult, lookup_doi
from pdf_download.naming import build_pdf_filename, iso_to_abbrev

logger = logging.getLogger(__name__)


@dataclass
class ArticleMeta:
    """從 .json sidecar 載入的單篇文章 metadata。"""
    doi: str
    pmid: str
    title: str
    authors: str
    article_type: str
    pages: str
    is_open_access: bool
    journal_abbrev: str           # 從 issue 層級複製過來
    publication_date: str          # 從 issue 層級複製過來
    journal_slug: str


@dataclass
class OrganizeResult:
    """單一 PDF 的處理結果。"""
    source: Path
    matched: bool
    doi: Optional[str] = None
    target: Optional[Path] = None
    article: Optional[ArticleMeta] = None
    reason: Optional[str] = None  # 失敗原因
    extract_method: Optional[str] = None  # filename / metadata / first_page


# DOI 正則：標準 DOI 格式
DOI_REGEX = re.compile(r"10\.\d{4,9}/[A-Za-z0-9.()_/-]+(?:[A-Za-z0-9])")


# 從檔名抽 DOI 的 publisher-specific patterns
# 每個 pattern 接收檔名 stem（去 .pdf）回傳 DOI 或 None
def _doi_from_nejm_filename(stem: str) -> Optional[str]:
    """NEJM: nejmoa2509761 → 10.1056/NEJMoa2509761"""
    m = re.match(r"(?i)^(nejm[a-z]{0,4})(\d+)$", stem)
    if not m:
        return None
    prefix_lower = m.group(1).lower()
    num = m.group(2)
    # 還原 NEJM 慣用大小寫: NEJM + oa/ra/cp/cpc/cps/p/c/e/icm
    suffix_map = {
        "nejmoa": "NEJMoa", "nejmra": "NEJMra", "nejmcp": "NEJMcp",
        "nejmcpc": "NEJMcpc", "nejmcps": "NEJMcps", "nejmp": "NEJMp",
        "nejmc": "NEJMc", "nejme": "NEJMe", "nejmicm": "NEJMicm",
        "nejm": "NEJM",
    }
    nejm_prefix = suffix_map.get(prefix_lower, prefix_lower.upper())
    return f"10.1056/{nejm_prefix}{num}"


def _doi_from_springer_filename(stem: str) -> Optional[str]:
    """Springer: s00134-026-08420-7 → 10.1007/s00134-026-08420-7

    注意：Springer 期刊用不同 publisher prefix（10.1007 / 10.1186 等）
    這個 fallback 只試最常見的 10.1007，沒中可以用 metadata 救。
    """
    if re.match(r"^s\d{4,5}-\d{3,4}-\d{4,5}-[\dxX]$", stem.lower()):
        return f"10.1007/{stem}"
    return None


def _doi_from_filename(name: str) -> Optional[str]:
    """嘗試各家 publisher 的檔名格式抽 DOI。回傳 None 表示沒中。"""
    stem = Path(name).stem
    for fn in (_doi_from_nejm_filename, _doi_from_springer_filename):
        doi = fn(stem)
        if doi:
            return doi
    # 通用：檔名直接出現 DOI 字串
    m = DOI_REGEX.search(stem)
    if m:
        return m.group(0).rstrip(".,;)")
    return None


def _doi_from_pdf_metadata(pdf: Path) -> Optional[str]:
    """讀 PDF /doi 或 /Subject 欄位。"""
    try:
        reader = PdfReader(str(pdf))
        meta = reader.metadata or {}
    except (PdfReadError, OSError, Exception) as e:
        logger.debug(f"讀 PDF metadata 失敗 {pdf.name}: {e}")
        return None

    for key in ("/doi", "/DOI", "/Doi", "/Subject", "/subject"):
        val = meta.get(key, "")
        if not val:
            continue
        m = DOI_REGEX.search(str(val))
        if m:
            return m.group(0).rstrip(".,;)")
    return None


def _doi_from_pdf_first_page(pdf: Path, max_chars: int = 15000) -> Optional[str]:
    """掃第一頁文字找 DOI。

    max_chars=15000：實測有些期刊（RBTI、MDPI、LWW）會把 DOI 印在
    第一頁中下方位置（>3000 字），舊上限 3000 會漏抓。15000 字足以
    覆蓋任何單頁文字量，不會誤掃到第二頁。
    """
    try:
        reader = PdfReader(str(pdf))
        if not reader.pages:
            return None
        text = reader.pages[0].extract_text() or ""
    except (PdfReadError, OSError, Exception) as e:
        logger.debug(f"讀 PDF 第一頁失敗 {pdf.name}: {e}")
        return None

    text = text[:max_chars]
    m = DOI_REGEX.search(text)
    if m:
        return m.group(0).rstrip(".,;)")
    return None


def extract_doi(pdf: Path) -> tuple[Optional[str], Optional[str]]:
    """嘗試三種方式抽 DOI。回傳 (doi, method)。"""
    doi = _doi_from_filename(pdf.name)
    if doi:
        return doi, "filename"

    doi = _doi_from_pdf_metadata(pdf)
    if doi:
        return doi, "metadata"

    doi = _doi_from_pdf_first_page(pdf)
    if doi:
        return doi, "first_page"

    return None, None


# ---------- 主流程 ----------

def _meta_from_online(online: LookupResult) -> ArticleMeta:
    """把 PubMed 線上查回來的 LookupResult 轉成 ArticleMeta。

    journal_abbrev 用 naming.iso_to_abbrev() 解析（先查 registry，
    再查 EXTENDED_ABBREV_MAP，最後 slugify）。
    """
    return ArticleMeta(
        doi=online.doi,
        pmid=online.pmid,
        title=online.title,
        authors=online.authors,
        article_type=online.article_type,
        pages=online.pages,
        is_open_access=online.is_open_access,
        journal_abbrev=iso_to_abbrev(online.journal_iso),
        publication_date=online.publication_date,
        journal_slug="",  # 線上查的沒有 slug，留空
    )


def build_doi_index(inbox_root: Path, max_recent_dirs: int = 8) -> Dict[str, ArticleMeta]:
    """掃 inbox_root 底下所有日期資料夾的 .json sidecar，建 DOI → ArticleMeta 索引。

    DOI 統一存小寫 key（filename 抽出來通常是小寫，PubMed 是大小寫混雜）。
    只看最近 max_recent_dirs 個資料夾，避免越看越久。
    """
    index: Dict[str, ArticleMeta] = {}

    # 找出所有 YYYY-MM-DD 格式的子資料夾
    date_dirs = sorted(
        [d for d in inbox_root.iterdir()
         if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)],
        reverse=True,
    )[:max_recent_dirs]

    for date_dir in date_dirs:
        for jf in date_dir.glob("*.json"):
            if jf.name == "INDEX.json":  # 萬一未來有
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"讀 {jf} 失敗: {e}")
                continue

            journal_abbrev = data.get("journal_abbrev", "")
            journal_slug = data.get("journal_slug", "")
            pub_date = data.get("publication_date", "")

            for art in data.get("articles", []):
                doi = art.get("doi", "")
                if not doi:
                    continue
                meta = ArticleMeta(
                    doi=doi,
                    pmid=art.get("pmid", ""),
                    title=art.get("title", ""),
                    authors=art.get("authors", ""),
                    article_type=art.get("article_type", ""),
                    pages=art.get("pages", ""),
                    is_open_access=art.get("is_open_access", False),
                    journal_abbrev=journal_abbrev,
                    publication_date=pub_date,
                    journal_slug=journal_slug,
                )
                # 用小寫做 key 避免大小寫不一致
                index[doi.lower()] = meta

    logger.info(f"DOI 索引建好，共 {len(index)} 篇來自 {len(date_dirs)} 個日期資料夾")
    return index


def organize_pdfs(
    inbox_root: Path,
    kb_raw_dir: Path,
    naming_config: dict,
    dry_run: bool = False,
    online_lookup: bool = True,
) -> List[OrganizeResult]:
    """把 inbox_root/_pdfs/ 的 PDF 處理完，回傳結果清單。

    Args:
        online_lookup: 當 inbox cache 找不到 DOI 時，是否打 PubMed 線上查 metadata。
                       關掉的話，找不到的 PDF 一律留在 _pdfs/。
    """
    pdfs_dir = inbox_root / "_pdfs"
    if not pdfs_dir.exists():
        raise RuntimeError(f"_pdfs/ 不存在: {pdfs_dir}")

    # 1) 建索引
    doi_index = build_doi_index(inbox_root)
    if not doi_index and not online_lookup:
        raise RuntimeError(
            "DOI 索引是空的且 online_lookup 關閉。"
            "請先跑 fetch --force 重新產生 sidecar，或拿掉 --no-online-lookup。"
        )

    # 2) 列出 PDF
    pdfs = [p for p in sorted(pdfs_dir.glob("*.pdf"))
            if not p.name.startswith(".")]
    if not pdfs:
        logger.info("_pdfs/ 沒有檔案要處理")
        return []

    logger.info(f"找到 {len(pdfs)} 個 PDF 要處理")

    # 3) 逐一處理
    if not dry_run:
        kb_raw_dir.mkdir(parents=True, exist_ok=True)

    results: List[OrganizeResult] = []
    max_title_chars = naming_config.get("max_title_chars", 35)
    stopwords = naming_config.get("stopwords")

    for pdf in pdfs:
        result = OrganizeResult(source=pdf, matched=False)

        # 抽 DOI
        doi, method = extract_doi(pdf)
        if not doi:
            result.reason = "DOI 抽取失敗（檔名 / metadata / 第一頁都沒找到）"
            results.append(result)
            continue

        result.doi = doi
        result.extract_method = method

        # 對索引
        meta = doi_index.get(doi.lower())
        if not meta and online_lookup:
            # Fallback：打 PubMed 線上查
            logger.info(f"  cache miss，線上查 PubMed: {doi}")
            online_meta = lookup_doi(doi)
            if online_meta:
                meta = _meta_from_online(online_meta)
                logger.info(f"  ✓ PubMed 找到 → {meta.journal_abbrev} ({online_meta.journal_iso})")

        if not meta:
            if online_lookup:
                result.reason = "DOI 不在 inbox 索引，PubMed 也查不到"
            else:
                result.reason = "DOI 不在 inbox 索引（線上查 disabled）"
            results.append(result)
            continue

        result.article = meta

        # 組目標檔名
        year = meta.publication_date[:4] if meta.publication_date else "unknown"
        new_name = build_pdf_filename(
            year=year,
            journal_abbrev=meta.journal_abbrev,
            title=meta.title,
            article_type=meta.article_type,
            max_title_chars=max_title_chars,
            stopwords=stopwords,
        )
        target = kb_raw_dir / new_name

        # 衝突檢查
        if target.exists():
            result.target = target
            result.reason = f"目標檔已存在: {new_name}"
            results.append(result)
            continue

        result.target = target
        result.matched = True

        if not dry_run:
            try:
                shutil.move(str(pdf), str(target))
            except OSError as e:
                result.matched = False
                result.reason = f"搬移失敗: {e}"
                results.append(result)
                continue

        results.append(result)

    return results


def write_log(
    results: List[OrganizeResult],
    inbox_root: Path,
    kb_raw_dir: Path,
    dry_run: bool,
) -> Path:
    """產 organize-log.md 到 inbox 最新日期資料夾。"""
    # 找最新日期資料夾，沒有就用今天
    date_dirs = sorted(
        [d for d in inbox_root.iterdir()
         if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)],
        reverse=True,
    )
    log_dir = date_dirs[0] if date_dirs else (inbox_root / datetime.now().strftime("%Y-%m-%d"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "organize-log.md"

    matched = [r for r in results if r.matched]
    unmatched = [r for r in results if not r.matched]

    lines = [
        f"# Organize Log · {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"來源: `{inbox_root / '_pdfs'}`",
        f"目標: `{kb_raw_dir}`",
        f"{'**(DRY RUN — 沒實際搬檔)**' if dry_run else ''}",
        "",
        f"處理 {len(results)} 個 PDF · 成功 {len(matched)} · 失敗/略過 {len(unmatched)}",
        "",
    ]

    if matched:
        lines.append(f"## ✅ 成功改名{'（dry run）' if dry_run else '並搬到 00-Raw/'} ({len(matched)})")
        lines.append("")
        lines.append("| 原檔名 | 新檔名 | DOI | 方式 |")
        lines.append("|--|--|--|--|")
        for r in matched:
            lines.append(
                f"| `{r.source.name}` | `{r.target.name}` | "
                f"[{r.doi}](https://doi.org/{r.doi}) | {r.extract_method} |"
            )
        lines.append("")

    if unmatched:
        lines.append(f"## ⚠️  Match 不到、留在 _pdfs/ ({len(unmatched)})")
        lines.append("")
        for r in unmatched:
            doi_part = f"DOI: `{r.doi}`" if r.doi else "DOI: 未知"
            lines.append(f"- `{r.source.name}` — {doi_part} — {r.reason}")
        lines.append("")

    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path
