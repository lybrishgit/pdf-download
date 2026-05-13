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
  6. 沒 match 到的留在 _pdfs/，全部寫進 organize-log_HHMM.md（每次跑不覆蓋）
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

from pdf_download.lookup import lookup_doi
from pdf_download.metadata import (
    ArticleMeta,
    extract_doi,
    meta_from_pubmed,
)
from pdf_download.naming import build_pdf_filename

logger = logging.getLogger(__name__)


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
    # 額外副本：成功複製到 extra_copy_dir 的目標路徑；
    # None = 沒設定 / 主流程沒成功 / 副本步驟失敗（看 extra_copy_note）
    extra_copy_path: Optional[Path] = None
    extra_copy_note: Optional[str] = None  # 例：「已存在略過」「複製失敗: ...」
    # _unmatched 副本：article metadata 拿不到時，原檔複製一份到 inbox/_unmatched/
    # 方便使用者之後手動 review；原檔仍留在 _pdfs/，下次 organize 還會再試
    unmatched_copy_path: Optional[Path] = None
    unmatched_copy_note: Optional[str] = None


# ---------- 主流程 ----------

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


def _copy_to_unmatched(
    pdf: Path,
    unmatched_dir: Optional[Path],
    result: OrganizeResult,
    dry_run: bool,
) -> None:
    """把 metadata 拿不到的 PDF 複製一份到 _unmatched/，記到 result。

    用原檔名直接放（不改名），方便使用者之後人工 review。已存在就略過、
    失敗只記 warning 不影響主流程。
    """
    if unmatched_dir is None:
        return
    target = unmatched_dir / pdf.name
    if target.exists():
        result.unmatched_copy_note = "已存在略過"
        return
    if dry_run:
        result.unmatched_copy_path = target
        result.unmatched_copy_note = "(dry-run)"
        return
    try:
        shutil.copy2(str(pdf), str(target))
        result.unmatched_copy_path = target
    except OSError as e:
        result.unmatched_copy_note = f"複製失敗: {e}"
        logger.warning(f"  _unmatched 副本複製失敗 {target}: {e}")


def organize_pdfs(
    inbox_root: Path,
    kb_raw_dir: Path,
    naming_config: dict,
    dry_run: bool = False,
    online_lookup: bool = True,
    extra_copy_dir: Optional[Path] = None,
) -> List[OrganizeResult]:
    """把 inbox_root/_pdfs/ 的 PDF 處理完，回傳結果清單。

    Args:
        online_lookup: 當 inbox cache 找不到 DOI 時，是否打 PubMed 線上查 metadata。
                       關掉的話，找不到的 PDF 一律留在 _pdfs/。
        extra_copy_dir: 若給定，每篇成功 organize 的 PDF 會複製一份到這個資料夾。
                        失敗只記 warning 不影響主流程；目標已存在就跳過。
                        用途：跨裝置同步待讀清單（如 iCloud Drive 給 iPad / iPhone）。
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
    unmatched_dir: Optional[Path] = inbox_root / "_unmatched"
    if not dry_run:
        kb_raw_dir.mkdir(parents=True, exist_ok=True)
        if extra_copy_dir:
            try:
                extra_copy_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.warning(f"額外副本資料夾建立失敗，本次跳過複製：{extra_copy_dir} ({e})")
                extra_copy_dir = None
        try:
            unmatched_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"_unmatched 資料夾建立失敗，本次跳過備份：{unmatched_dir} ({e})")
            unmatched_dir = None

    results: List[OrganizeResult] = []
    max_title_chars = naming_config.get("max_title_chars", 35)
    stopwords = naming_config.get("stopwords")

    for pdf in pdfs:
        result = OrganizeResult(source=pdf, matched=False)

        # 抽 DOI
        doi, method = extract_doi(pdf)
        if not doi:
            result.reason = "DOI 抽取失敗（檔名 / metadata / 第一頁都沒找到）"
            _copy_to_unmatched(pdf, unmatched_dir, result, dry_run)
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
                meta = meta_from_pubmed(online_meta)
                logger.info(f"  ✓ PubMed 找到 → {meta.journal_abbrev} ({online_meta.journal_iso})")

        if not meta:
            if online_lookup:
                result.reason = "DOI 不在 inbox 索引，PubMed 也查不到"
            else:
                result.reason = "DOI 不在 inbox 索引（線上查 disabled）"
            _copy_to_unmatched(pdf, unmatched_dir, result, dry_run)
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

        # 額外副本：搬到 KB 後再從 target 複製過去（避免讀已不存在的 source）
        if extra_copy_dir:
            extra_target = extra_copy_dir / new_name
            if extra_target.exists():
                result.extra_copy_note = "已存在略過"
                logger.info(f"  副本已存在，略過：{extra_target}")
            elif dry_run:
                result.extra_copy_path = extra_target
                result.extra_copy_note = "(dry-run)"
            else:
                try:
                    shutil.copy2(str(target), str(extra_target))
                    result.extra_copy_path = extra_target
                except OSError as e:
                    # 副本失敗不影響主流程，只記 warning
                    result.extra_copy_note = f"複製失敗: {e}"
                    logger.warning(f"  副本複製失敗 {extra_target}: {e}")

        results.append(result)

    return results


def write_log(
    results: List[OrganizeResult],
    inbox_root: Path,
    kb_raw_dir: Path,
    dry_run: bool,
) -> Path:
    """產 organize-log_HHMM.md 到 inbox 最新日期資料夾（每次跑不覆蓋）。"""
    # 找最新日期資料夾，沒有就用今天
    date_dirs = sorted(
        [d for d in inbox_root.iterdir()
         if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}$", d.name)],
        reverse=True,
    )
    log_dir = date_dirs[0] if date_dirs else (inbox_root / datetime.now().strftime("%Y-%m-%d"))
    log_dir.mkdir(parents=True, exist_ok=True)
    # 加 HHMM 避免同一天多次跑覆蓋（launchd 03:00 + 手動偶爾跑）
    log_path = log_dir / f"organize-log_{datetime.now().strftime('%H%M')}.md"

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
            if r.unmatched_copy_path:
                lines.append(f"    - 📎 已複製到 `{r.unmatched_copy_path}`")
            elif r.unmatched_copy_note:
                lines.append(f"    - 📎 _unmatched 副本：{r.unmatched_copy_note}")
        lines.append("")

    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path
