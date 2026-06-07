"""PDF 命名規則。

遵循 med-literature-organizer skill 的格式：
    [年份]_[期刊縮寫]_[主題關鍵字].pdf

我們有乾淨的 metadata，所以不需要 skill 那套啟發式判斷，
只是字串組裝 + slugify 標題。
"""

from __future__ import annotations

import re
from typing import Iterable

# PubMed ISO Abbreviation → 顯示用縮寫（沿用 med-literature-organizer skill 的命名習慣）
# 11 本主期刊在 registry.py 各自的 abbrev 已涵蓋；這裡只放次專科常見的。
# key 用小寫比對。
EXTENDED_ABBREV_MAP = {
    "lancet respir med": "LancetRespir",
    "lancet oncol": "LancetOncol",
    "lancet infect dis": "LancetID",
    "lancet glob health": "LancetGlobalHealth",
    "lancet diabetes endocrinol": "LancetDiabEndo",
    "jama intern med": "JAMAInternMed",
    "jama netw open": "JAMANetworkOpen",
    "jama oncol": "JAMAOncol",
    "jama cardiol": "JAMACardiol",
    "jama neurol": "JAMANeurol",
    "jama surg": "JAMASurg",
    "jama pediatr": "JAMAPediatr",
    "bmj open": "BMJOpen",
    "bmj open respir res": "BMJOpenRespir",
    "ann am thorac soc": "AnnATS",
    "ats sch": "ATSScholar",
    "crit care": "CritCare",
    "crit care explor": "CritCareExplor",
    "crit care clin": "CritCareClin",
    "j crit care": "JCritCare",
    "respir care": "RespirCare",
    "respir med": "RespirMed",
    "j thorac oncol": "JThoracOncol",
    "j clin oncol": "JCO",
    "clin chest med": "ClinChestMed",
    "clin infect dis": "CID",
    "antimicrob agents chemother": "AAC",
    "antimicrob resist infect control": "AntimicrobResistInfectControl",
    "j microbiol immunol infect": "JMII",
    "eur j intern med": "EurJInternMed",
    "radiology": "Radiology",
    "radiographics": "RadioGraphics",
    "ajr am j roentgenol": "AJR",
    "korean j radiol": "KoreanJRadiol",
    "cochrane database syst rev": "Cochrane",
    "plos one": "PLoSOne",
    "front public health": "FrontPublicHealth",
    "nat med": "NatMed",
    "nat aging": "NatureAging",
    "nat rev neurol": "NatRevNeurol",
    "n engl j med evid": "NEJMEvid",
    "medicine (baltimore)": "Medicine",
    "chron respir dis": "ChronRespirDis",
    "expert rev clin pharmacol": "ExpertRevClinPharmacol",
    "respir res": "RespirRes",
    # 通用大刊（讓三支改名工具涵蓋一致）
    "nature": "Nature",
    "science": "Science",
    "cell": "Cell",
}


def iso_to_abbrev(iso_abbrev: str) -> str:
    """PubMed ISO abbrev → 顯示用縮寫。

    順序：
      1. 先查 11 本期刊 registry（精確比對 ISO abbrev）
      2. 再查 EXTENDED_ABBREV_MAP（次專科常見）
      3. fallback：去掉空格與點號（"N Engl J Med" → "NEnglJMed"）

    放這裡而不是 registry.py 是為了避免 circular import。
    """
    # 1. 主期刊 registry
    try:
        from pdf_download.journals.registry import JOURNALS
        for cfg in JOURNALS.values():
            if cfg.iso_abbrev.lower() == iso_abbrev.lower():
                return cfg.abbrev
    except ImportError:
        pass

    # 2. Extended map
    if iso_abbrev.lower() in EXTENDED_ABBREV_MAP:
        return EXTENDED_ABBREV_MAP[iso_abbrev.lower()]

    # 3. Slugify: 去掉空格與點號（保留大小寫）
    return re.sub(r"[.\s]+", "", iso_abbrev) or "Unknown"

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
    # 強制轉字串：防 YAML 1.1 把 on/off/yes/no 解析成 Boolean 漏進來
    sw = set(str(w) for w in stopwords) if stopwords is not None else DEFAULT_STOPWORDS

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


# ──────────────────────────────────────────────────────────────────
# 類型後綴對照表（命名規則的「真理來源」）
# ──────────────────────────────────────────────────────────────────
# 順序很重要：更具體的擺前面，先命中先回（例如 meta-analysis 常同時被
# PubMed 標成 "Systematic Review"，但我們要先判定成 _MA）。
#
# ⚠️ 同步提醒：以下兩支獨立腳本各自保有一份「逐字相同」的副本，
#    改這裡記得一起改：
#      - projects/downloads-organizer/scripts/medical_rename.py
#      - projects/pdf-rename-claude/rename_pdf.py
TYPE_SUFFIX_MAP = [
    ("meta-analysis", "_MA"),
    ("meta analysis", "_MA"),
    ("systematic review", "_SR"),
    ("randomized controlled trial", "_RCT"),
    ("practice guideline", "_Guideline"),
    ("guideline", "_Guideline"),
    ("recommendation", "_Guideline"),
    ("review", "_Review"),
    ("case reports", "_CaseReport"),
    ("case report", "_CaseReport"),
    ("editorial", "_Editorial"),
    ("perspective", "_Perspective"),
    ("letter", "_Letter"),
]


def article_type_suffix(article_type: str) -> str:
    """從 article type 推檔名後綴（B 案：細分證據類型）。

    輸入可以是單一字串或多個 publication type 串接而成的字串，
    一律小寫子字串比對，依 TYPE_SUFFIX_MAP 順序先命中先回。
    """
    if not article_type:
        return ""
    t = article_type.lower()
    for needle, suffix in TYPE_SUFFIX_MAP:
        if needle in t:
            return suffix
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
