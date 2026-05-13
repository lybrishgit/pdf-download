"""原地改名：給任意路徑的 PDF 套用 KB 命名規則。

跟 organize 的差別：
  - organize  → 處理 inbox/_pdfs/ 的檔案，搬到 KB 00-Raw/，依賴 sidecar cache
  - rename    → 處理任意路徑（單檔或資料夾），原地改名，純線上查 PubMed

使用情境：
  - Mac 上隨手有一批 PDF（Desktop / Downloads / 雲端硬碟）想清檔名
  - 不想進醫學資料庫流程，只是要體面檔名
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from pdf_download.lookup import lookup_doi
from pdf_download.metadata import (
    ArticleMeta,
    extract_doi,
    meta_from_pubmed,
)
from pdf_download.naming import build_pdf_filename

logger = logging.getLogger(__name__)


@dataclass
class RenameResult:
    """單一 PDF 的改名結果。"""
    source: Path
    target: Optional[Path] = None        # 期望/實際改成的路徑（同資料夾）
    matched: bool = False                # 找得到 DOI 且能查到 metadata
    renamed: bool = False                # 真的改了（apply 模式才會 True）
    already_correct: bool = False        # 現有檔名已等於新名稱，不需動
    doi: Optional[str] = None
    extract_method: Optional[str] = None
    article: Optional[ArticleMeta] = None
    reason: Optional[str] = None         # 失敗或略過原因


def _collect_pdfs(paths: List[Path]) -> List[Path]:
    """攤平輸入路徑：單檔保留、資料夾掃 *.pdf（不遞迴）。"""
    out: List[Path] = []
    for p in paths:
        if not p.exists():
            logger.warning(f"路徑不存在，跳過：{p}")
            continue
        if p.is_file():
            if p.suffix.lower() == ".pdf":
                out.append(p)
            else:
                logger.warning(f"不是 PDF，跳過：{p}")
        elif p.is_dir():
            out.extend(sorted(
                f for f in p.glob("*.pdf")
                if not f.name.startswith(".")
            ))
    return out


def _resolve_conflict(target: Path, source: Path) -> Path:
    """目標檔已存在 → 加 _dup / _dup2 後綴避免覆蓋。

    若目標就是 source 本身（同檔），回傳原 target，由呼叫端判斷
    為 already_correct。
    """
    if not target.exists():
        return target
    try:
        if target.resolve() == source.resolve():
            return target  # 同一檔，呼叫端會處理
    except OSError:
        pass

    stem, suffix = target.stem, target.suffix
    for n in range(2, 100):
        candidate = target.with_name(
            f"{stem}_dup{'' if n == 2 else n}{suffix}"
        )
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"連 _dup99 都被佔用，放棄：{target}")


def rename_pdfs(
    paths: List[Path],
    naming_config: dict,
    apply: bool = False,
    online_lookup: bool = True,
) -> List[RenameResult]:
    """掃 paths（單檔/資料夾），抽 DOI、查 metadata、產生新檔名。

    Args:
        paths: 任意混合的檔案或資料夾路徑
        naming_config: 同 organize，從 config.yaml 的 naming 區段傳入
        apply: False = dry-run（只計算建議檔名）；True = 實際改名
        online_lookup: True 時 DOI 都靠 PubMed 線上查；False 時只看
                       檔名/metadata/第一頁能不能直接抽到 DOI（不查線上）

    Returns:
        每個 PDF 的 RenameResult 清單。
    """
    pdfs = _collect_pdfs(paths)
    if not pdfs:
        return []

    max_title_chars = naming_config.get("max_title_chars", 35)
    stopwords = naming_config.get("stopwords")

    results: List[RenameResult] = []

    for pdf in pdfs:
        result = RenameResult(source=pdf)

        # 1) 抽 DOI
        doi, method = extract_doi(pdf)
        if not doi:
            result.reason = "DOI 抽取失敗（檔名 / metadata / 第一頁都沒找到）"
            results.append(result)
            continue
        result.doi = doi
        result.extract_method = method

        # 2) PubMed 查 metadata
        if not online_lookup:
            result.reason = "online_lookup 關閉、無 inbox cache 可用"
            results.append(result)
            continue

        online_meta = lookup_doi(doi)
        if not online_meta:
            result.reason = f"PubMed 查不到 DOI：{doi}"
            results.append(result)
            continue
        meta = meta_from_pubmed(online_meta)
        result.article = meta

        # 3) 組新檔名
        year = meta.publication_date[:4] if meta.publication_date else "unknown"
        new_name = build_pdf_filename(
            year=year,
            journal_abbrev=meta.journal_abbrev,
            title=meta.title,
            article_type=meta.article_type,
            max_title_chars=max_title_chars,
            stopwords=stopwords,
        )
        target = pdf.parent / new_name

        # 4) 已經是正確檔名？
        if pdf.name == new_name:
            result.matched = True
            result.target = target
            result.already_correct = True
            results.append(result)
            continue

        # 5) 衝突處理
        target = _resolve_conflict(target, pdf)
        result.target = target
        result.matched = True

        # 6) 實際改名（apply 模式才動）
        if apply:
            try:
                shutil.move(str(pdf), str(target))
                result.renamed = True
            except OSError as e:
                result.matched = False
                result.reason = f"改名失敗: {e}"

        results.append(result)

    return results
