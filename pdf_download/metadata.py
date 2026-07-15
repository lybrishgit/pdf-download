"""DOI 抽取與文章 metadata 共用模組。

被 organize.py 和 rename.py 共用，避免互相 import。

公開 API：
  - ArticleMeta：文章 metadata dataclass
  - extract_doi(pdf): 三段式抽 DOI（檔名 → PDF metadata → 第一頁）
  - meta_from_pubmed(lookup_result): PubMed 查回來的東西轉成 ArticleMeta
  - DOI_REGEX：標準 DOI 正則（給其他模組想客製化抽取用）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PyPDF2 import PdfReader
from PyPDF2.errors import PdfReadError

from pdf_download.lookup import LookupResult
from pdf_download.naming import iso_to_abbrev

logger = logging.getLogger(__name__)


@dataclass
class ArticleMeta:
    """文章 metadata。

    來源可能是 inbox sidecar JSON 或 PubMed 線上查；兩種都先 normalize
    成這個 shape 再餵給命名邏輯。
    """
    doi: str
    pmid: str
    title: str
    authors: str
    article_type: str
    pages: str
    is_open_access: bool
    journal_abbrev: str           # 通常從 issue 層級複製，或從 ISO abbrev 推
    publication_date: str          # YYYY-MM-DD（PubMed 缺日期會回 YYYY-MM-01）
    journal_slug: str              # 來自 sidecar 的 slug；PubMed 來源留空


# 標準 DOI 正則：10.<registrant>/<suffix>
DOI_REGEX = re.compile(r"10\.\d{4,9}/[A-Za-z0-9.()_/-]+(?:[A-Za-z0-9])")

# 預印本平台的 DOI 前綴。正式論文的 PDF 首頁常印著預印本浮水印（作者先投 medRxiv
# 再正式發表），於是同一頁上有兩個 DOI。直接取「第一個」會抓到預印本那個，
# 用錯誤身分歸檔——檔名/期刊/年份全錯，而且無聲無息。挑 DOI 時把這些往後排。
_PREPRINT_PREFIXES = ("10.1101", "10.21203", "10.20944", "10.31234", "10.31219")

# DOI 後面直接黏著英文字：PDF 抽文字時 DOI 跟下一個字之間常沒有空白，正則就一路
# 吃過去（實測：`...223539Protected`、`...007061Copyright`、`...942doi`）。
# 規則：數字後面緊接「大寫開頭的英文單字」或小寫 doi ＝黏上來的，砍掉。
# 限定「數字後面」是為了避開 10.1056/NEJMoa2509761 這種字母在數字前的正常 DOI。
_GLUED_TAIL = re.compile(r"(?<=\d)(?:[A-Z][a-z]{2,}|doi)\w*$")

# PDF 抽字會在 DOI 中間塞進假空白。實測 ERJ 首頁長這樣：
#   `(https://doi.org/10.1183/13993003.01570 -2025)`   ← 連字號前多一個空白
# 正則吃不過空白 → 只抽到 `...01570`（少了 -2025）→ PubMed 查無此篇。
# 只補「DOI 尾端數字 ＋ 空白 ＋ 連字號緊接四位年份」這一種形狀，且**連字號後面不准
# 有空白**——這樣頁面上正常排版的 `10.1097/CCM.xxx - 2026 Lippincott` 不會被誤接成
# DOI 的一部分。\s+ 同時涵蓋空白與換行兩種斷法。
_DOI_YEAR_GAP = re.compile(r"(10\.\d{4,9}/[A-Za-z0-9.()_/-]*\d)\s+-(\d{4})(?![\d-])")


def _clean_doi(raw: str) -> str:
    """把正則多吃進來的尾巴清掉。"""
    doi = raw.rstrip(".,;)")
    doi = _GLUED_TAIL.sub("", doi)
    return doi.rstrip(".,;)")


def _pick_doi(text: str) -> Optional[str]:
    """從一段文字挑出最可能是「這篇論文本身」的 DOI。

    收集全部候選再挑，而不是取第一個：正式期刊的優先於預印本浮水印。
    整頁只有預印本 DOI 時，那它就是本體，照用。
    """
    # 先補 DOI 中間的假空白（見 _DOI_YEAR_GAP）。放這層而非只放首頁那條，
    # 是因為 PDF metadata 欄位一樣可能有這個問題，而且放這裡才測得到。
    text = _DOI_YEAR_GAP.sub(r"\1-\2", text)
    cands = [c for c in (_clean_doi(m.group(0)) for m in DOI_REGEX.finditer(text)) if c]
    if not cands:
        return None
    for c in cands:
        if not c.startswith(_PREPRINT_PREFIXES):
            return c
    return cands[0]


# ---------- 從檔名抽 DOI（publisher-specific patterns） ----------

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


def _doi_from_underscore_filename(stem: str) -> Optional[str]:
    """出版商下載檔名把 DOI 的 `/` 換成 `_`（斜線不能當檔名）：
    10.1097_ccm.0000000000007091 → 10.1097/ccm.0000000000007091

    只在「前綴後面剛好一個底線」時還原。多個底線分不出哪個才是斜線
    （10.1093_ajrccm_aamaf105 可能是 10.1093/ajrccm/aamaf105），而檔名這條
    跑在首頁抽取之前，猜錯會回傳錯 DOI、反而害到本來靠首頁就能成功的檔案。
    分不出來時寧可不猜，讓它往下走首頁那條。
    """
    m = re.match(r"^(10\.\d{4,9})_([^_]+)$", stem)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _doi_from_filename(name: str) -> Optional[str]:
    """嘗試各家 publisher 的檔名格式抽 DOI。回傳 None 表示沒中。"""
    stem = Path(name).stem
    for fn in (_doi_from_nejm_filename, _doi_from_springer_filename,
               _doi_from_underscore_filename):
        doi = fn(stem)
        if doi:
            return doi
    # 通用：檔名直接出現 DOI 字串
    m = DOI_REGEX.search(stem)
    if m:
        return _clean_doi(m.group(0))
    return None


# ---------- 從 PDF 內容抽 DOI ----------

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
        doi = _pick_doi(str(val))
        if doi:
            return doi
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

    return _pick_doi(text[:max_chars])


def extract_doi(pdf: Path) -> Tuple[Optional[str], Optional[str]]:
    """三段式抽 DOI。回傳 (doi, method)，method 為 filename / metadata / first_page。"""
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


# ---------- PubMed LookupResult → ArticleMeta ----------

def meta_from_pubmed(online: LookupResult) -> ArticleMeta:
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
