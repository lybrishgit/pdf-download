"""期刊登記表。

每本期刊只是一筆設定，所有抓取邏輯共用 pubmed.py。

關鍵欄位：
- iso_abbrev: PubMed/MEDLINE 標準縮寫，用在 esearch 的 [Journal] 條件
- abbrev:    我們要在檔名/標題用的縮寫（沿用 med-literature-organizer skill）
- full_name: 完整名稱
- pdf_url:   給定 DOI 後 PDF/原文的網址 pattern（用 Python format string {doi}）
- article_url: 同上（兩者目前一致，留欄位以便將來個別調整）
- cadence:   "weekly" / "biweekly" / "monthly" — 影響搜尋的日期窗口

設計決策：pdf_url 統一用 https://doi.org/{doi}
=================================================
原本想為每本期刊組「直接 PDF 下載」連結，但實測發現：

- LWW (CCM)：用內部 accession number，DOI 推不出來
- BMJ / Thorax / ERJ：要 volume/issue/first_page，AOP 文章沒有
- JAMA / Wolters Kluwer：用內部 article ID，DOI 推不出來
- Lancet：用 PII（PubMed 有提供，但仍是少數 case）

每家規則不同、容易壞、PubMed 索引中文章資訊不完整時更脆弱。

統一用 doi.org 的好處：
- 永遠正確（DOI 是 IDF 維護的全球識別碼）
- 你機構 VPN + EZproxy 本來就會接管 doi.org 重新導向
- 少一個 URL pattern 要維護，少一個會壞的點
- 多一次點擊（先到文章頁再點 PDF），但機構認證流程本來就要這一跳
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JournalConfig:
    slug: str
    iso_abbrev: str          # PubMed [Journal] 條件用
    abbrev: str              # 檔名/顯示用
    full_name: str
    pdf_url: str             # 含 {doi} 佔位符
    article_url: str         # 含 {doi} 佔位符
    cadence: str             # weekly / biweekly / monthly


# 共用 URL pattern：doi.org 是最穩的選擇
DOI_URL = "https://doi.org/{doi}"


JOURNALS = {
    "nejm": JournalConfig(
        slug="nejm",
        iso_abbrev="N Engl J Med",
        abbrev="NEJM",
        full_name="New England Journal of Medicine",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="weekly",
    ),
    "jama": JournalConfig(
        slug="jama",
        iso_abbrev="JAMA",
        abbrev="JAMA",
        full_name="JAMA",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="weekly",
    ),
    "lancet": JournalConfig(
        slug="lancet",
        iso_abbrev="Lancet",
        abbrev="Lancet",
        full_name="The Lancet",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="weekly",
    ),
    "bmj": JournalConfig(
        slug="bmj",
        iso_abbrev="BMJ",
        abbrev="BMJ",
        full_name="The BMJ",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="weekly",
    ),
    "annim": JournalConfig(
        slug="annim",
        iso_abbrev="Ann Intern Med",
        abbrev="AnnIntMed",
        full_name="Annals of Internal Medicine",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="monthly",
    ),
    "chest": JournalConfig(
        slug="chest",
        iso_abbrev="Chest",
        abbrev="Chest",
        full_name="CHEST",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="monthly",
    ),
    "ajrccm": JournalConfig(
        slug="ajrccm",
        iso_abbrev="Am J Respir Crit Care Med",
        abbrev="AJRCCM",
        full_name="American Journal of Respiratory and Critical Care Medicine",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="biweekly",
    ),
    "icm": JournalConfig(
        slug="icm",
        iso_abbrev="Intensive Care Med",
        abbrev="ICM",
        full_name="Intensive Care Medicine",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="monthly",
    ),
    "ccm": JournalConfig(
        slug="ccm",
        iso_abbrev="Crit Care Med",
        abbrev="CCM",
        full_name="Critical Care Medicine",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="monthly",
    ),
    "thorax": JournalConfig(
        slug="thorax",
        iso_abbrev="Thorax",
        abbrev="Thorax",
        full_name="Thorax",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="monthly",
    ),
    "erj": JournalConfig(
        slug="erj",
        iso_abbrev="Eur Respir J",
        abbrev="ERJ",
        full_name="European Respiratory Journal",
        pdf_url=DOI_URL,
        article_url=DOI_URL,
        cadence="monthly",
    ),
}
