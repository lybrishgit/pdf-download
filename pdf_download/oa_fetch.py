"""OA 全文自動下載：走公開索引階梯，把拿得到的 PDF 抓進 _pdfs/。

給 organize 接手用：抓下來的檔案用中性檔名放進 _pdfs/，隔天 03:00 的 organize
照既有流程（讀 PDF 首頁抽 DOI → 對索引 → 套 naming 規則）改名搬進 KB。
**刻意不碰 naming.py**（它有三支同步副本，動它成本高），走既有能力就好。

順序是實測決定的，不是憑直覺（2026-07-10，用 6/28+7/5 共 72 篇真實資料量測）：

    Semantic Scholar  37.5%   ← 有批次端點，1 次呼叫問完全部
    PMC (Europe PMC)  31.9%   ← 有批次端點，1 次呼叫問完全部
    Unpaywall          9.7%   ← 最弱且只能逐篇問，拿來補漏
    三者聯集          45.8%

所以先批次問 S2 + PMC，剩下沒解決的才逐篇問 Unpaywall（呼叫數 72+ → ~42）。
照 paper-fetch 那樣「Unpaywall 優先」對我們反而慢又低效。

兩個實測得出的關鍵設計：
  - **不用 is_open_access 當閘門**：聯集 33 篇 > PubMed 標 OA 的 24 篇，
    多出的是作者手稿 / 典藏版。對全部篇都走一次階梯。
  - **一定要驗 %PDF magic bytes**：付費牆與 CDN 很愛回 200 text/html 假裝成
    PDF 回應。這條跟 organize._ensure_local 是同一個教訓。

只走公開索引：無憑證、無登入、無反爬。任何一篇失敗就跳過並記 log，
絕不影響 fetch 主流程（同 --email 的原則）。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}"
EUROPEPMC_PDF = "https://europepmc.org/articles/{pmcid}?pdf=render"


@dataclass
class OAResult:
    """單篇 OA 下載結果。"""
    doi: str
    journal: str
    title: str
    source: Optional[str] = None   # s2 / pmc / unpaywall
    url: Optional[str] = None
    path: Optional[Path] = None
    ok: bool = False
    reason: Optional[str] = None   # 沒抓到的原因


def _session(email: str) -> requests.Session:
    s = requests.Session()
    # 禮貌：帶上聯絡信箱，讓各索引知道我們是誰（Unpaywall 也要求）
    s.headers["User-Agent"] = f"pdf-download/0.1 (mailto:{email})"
    return s


# ---------- 三條索引路由 ----------

def _s2_batch(sess: requests.Session, dois: List[str]) -> Dict[str, str]:
    """Semantic Scholar 批次問 openAccessPdf。回 {doi: pdf_url}。

    一次最多 500 個 id；失敗整批放棄（不影響其他路由）。
    """
    found: Dict[str, str] = {}
    for i in range(0, len(dois), 400):
        chunk = dois[i:i + 400]
        # S2 未認證的額度很緊，實測會回 429。它是命中率最高的一條（實測 37.5%），
        # 被一次 429 打掉太可惜，所以退避重試。真的過不了就交給 PMC / Unpaywall
        # 這兩條 fallback（實測 S2 掛掉時 Unpaywall 有把漏的撈回來）。
        for attempt in range(1, 4):
            try:
                r = sess.post(
                    S2_BATCH_URL,
                    params={"fields": "openAccessPdf"},
                    json={"ids": [f"DOI:{d}" for d in chunk]},
                    timeout=60,
                )
                if r.status_code == 429:
                    wait = 5 * attempt
                    logger.info(f"  S2 rate limit（429），等 {wait}s 重試 {attempt}/3...")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                for doi, res in zip(chunk, r.json()):
                    url = ((res or {}).get("openAccessPdf") or {}).get("url")
                    if url:
                        found[doi] = url
                break
            except (requests.RequestException, ValueError) as e:
                logger.warning(f"  S2 批次查詢失敗（跳過此路由）：{str(e)[:100]}")
                break
        else:
            logger.warning("  S2 重試 3 次仍 429，跳過此路由（交給 PMC / Unpaywall）")
        time.sleep(1.0)
    return found


def _pmc_batch(sess: requests.Session, dois: List[str]) -> Dict[str, str]:
    """NCBI idconv 批次 DOI→PMCID，有 PMCID 就組 Europe PMC 的 PDF 網址。

    一次最多 200 個 id；失敗整批放棄。
    """
    found: Dict[str, str] = {}
    for i in range(0, len(dois), 180):
        chunk = dois[i:i + 180]
        try:
            r = sess.get(
                IDCONV_URL,
                params={"ids": ",".join(chunk), "format": "json", "tool": "pdf-download"},
                timeout=40,
            )
            r.raise_for_status()
            for rec in r.json().get("records", []):
                doi = (rec.get("doi") or "").lower()
                pmcid = rec.get("pmcid")
                if doi and pmcid:
                    found[doi] = EUROPEPMC_PDF.format(pmcid=pmcid)
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"  PMC idconv 查詢失敗（跳過此路由）：{str(e)[:100]}")
        time.sleep(0.4)
    return found


def _unpaywall_one(sess: requests.Session, doi: str, email: str) -> Optional[str]:
    """逐篇問 Unpaywall，回第一個有 url_for_pdf 的位置。

    注意：Unpaywall 常說「是 OA」但只給落地頁（url_for_pdf 是 None）——
    實測 24 篇這種，其中 23 篇靠 S2/PMC 救回。所以這條只當補漏用。
    """
    try:
        r = sess.get(UNPAYWALL_URL.format(doi=doi), params={"email": email}, timeout=20)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        for loc in (r.json().get("oa_locations") or []):
            if loc and loc.get("url_for_pdf"):
                return loc["url_for_pdf"]
    except (requests.RequestException, ValueError) as e:
        logger.debug(f"  Unpaywall {doi} 失敗：{str(e)[:80]}")
    return None


def resolve_pdf_urls(sess: requests.Session, candidates: List[dict], email: str) -> Dict[str, tuple]:
    """對全部候選走階梯，回 {doi: (url, source)}。

    先批次（S2 → PMC），剩下的才逐篇 Unpaywall。
    """
    dois = [c["doi"] for c in candidates]
    resolved: Dict[str, tuple] = {}

    logger.info(f"  [1/3] Semantic Scholar 批次查 {len(dois)} 篇...")
    for doi, url in _s2_batch(sess, dois).items():
        resolved[doi] = (url, "s2")
    logger.info(f"        → {len(resolved)} 篇命中")

    todo = [d for d in dois if d not in resolved]
    logger.info(f"  [2/3] PMC 批次查剩下 {len(todo)} 篇...")
    before = len(resolved)
    for doi, url in _pmc_batch(sess, todo).items():
        resolved.setdefault(doi, (url, "pmc"))
    logger.info(f"        → 再命中 {len(resolved) - before} 篇")

    todo = [d for d in dois if d not in resolved]
    logger.info(f"  [3/3] Unpaywall 逐篇補漏 {len(todo)} 篇...")
    before = len(resolved)
    for doi in todo:
        url = _unpaywall_one(sess, doi, email)
        if url:
            resolved[doi] = (url, "unpaywall")
        time.sleep(0.1)
    logger.info(f"        → 再命中 {len(resolved) - before} 篇")

    return resolved


# ---------- 下載 ----------

def _safe_stem(journal: str, pmid: str, doi: str) -> str:
    """組中性檔名，故意不讓它長得像 DOI。

    naming._doi_from_filename 會先用 DOI 正則掃檔名；正則要求 `10.xxxx/` 這種
    有斜線的形狀，而檔名不可能有斜線，所以這裡怎麼組都不會誤觸發。
    用 PMID 當識別；沒有就用 DOI 去掉標點的尾段。
    """
    j = re.sub(r"[^A-Za-z0-9]", "", journal) or "OA"
    ident = pmid or re.sub(r"[^A-Za-z0-9]+", "-", doi)[-40:]
    return f"OA_{j}_{ident}"


def _download_pdf(sess: requests.Session, url: str, dest: Path,
                  timeout: int, max_bytes: int) -> tuple:
    """下載並驗證是真 PDF。回 (ok, reason)。

    驗 %PDF magic bytes 而不是信 Content-Type：付費牆 / CDN 會回
    200 text/html 假裝成 PDF 回應（跟 organize._ensure_local 同一個教訓）。
    """
    try:
        r = sess.get(url, timeout=timeout, allow_redirects=True, stream=True)
        r.raise_for_status()
        data = b""
        for chunk in r.iter_content(64 * 1024):
            data += chunk
            if len(data) > max_bytes:
                return False, f"超過大小上限（>{max_bytes // 1024 // 1024}MB）"
    except requests.RequestException as e:
        return False, f"下載失敗：{str(e)[:80]}"

    if not data.startswith(b"%PDF-"):
        head = data[:60].decode("utf-8", "replace").replace("\n", " ")
        return False, f"不是 PDF（可能是付費牆頁面）：{head[:40]!r}"
    if b"%%EOF" not in data[-2048:]:
        return False, f"PDF 不完整（{len(data)} bytes，結尾缺 %%EOF）"

    dest.write_bytes(data)
    return True, None


def _already_in_kb(c: dict, kb_raw_dir: Optional[Path], naming_config: dict) -> bool:
    """這篇是否已經在 KB 裡（待消化的 00-Raw/ 或已消化的 00-Raw/_processed/）。

    用 organize 同一套 build_pdf_filename 算檔名、同一個 kb_has_file 查存在——
    共用單一真相，不自己複製命名規則、也不自己判斷該查哪些目錄。
    算不出來就當作不在（寧可多抓，不可漏抓）。

    不做這個檢查的話：已在 KB 的篇會被重複下載 → organize 搬進 00-Raw →
    daily 再消化一次 → KB 產出重複筆記。
    """
    if not kb_raw_dir:
        return False
    try:
        from pdf_download.naming import build_pdf_filename
        from pdf_download.organize import kb_has_file
        name = build_pdf_filename(
            year=c.get("year") or "unknown",
            journal_abbrev=c.get("journal", ""),
            title=c.get("title", ""),
            article_type=c.get("article_type", ""),
            max_title_chars=naming_config.get("max_title_chars", 35),
            stopwords=naming_config.get("stopwords"),
        )
        return kb_has_file(kb_raw_dir, name)
    except Exception as e:
        logger.debug(f"  KB 去重檢查失敗（當作不在）：{e}")
        return False


def download_oa_fulltext(
    candidates: List[dict],
    pdfs_dir: Path,
    oa_config: dict,
    dry_run: bool = False,
    kb_raw_dir: Optional[Path] = None,
    naming_config: Optional[dict] = None,
) -> List[OAResult]:
    """對 candidates 走 OA 階梯並下載。

    candidates: [{"doi","pmid","journal","title","article_type","year"}, ...]
                （doi 必填、已小寫）
    kb_raw_dir: 給定就先去重——KB 已有的篇不重抓（見 _already_in_kb）。
    回傳每篇的 OAResult。任何失敗都只記錄、不拋出——不能影響 fetch 主流程。
    """
    email = oa_config.get("unpaywall_email") or ""
    if not email:
        raise RuntimeError("config 的 oa_download.unpaywall_email 沒填（Unpaywall 要求帶信箱）")
    timeout = int(oa_config.get("timeout", 60))
    max_bytes = int(oa_config.get("max_size_mb", 50)) * 1024 * 1024

    naming_config = naming_config or {}
    all_cands = [c for c in candidates if c.get("doi")]
    results = [OAResult(doi=c["doi"], journal=c.get("journal", "?"),
                        title=c.get("title", "")) for c in all_cands]
    by_doi = {r.doi: r for r in results}
    if not all_cands:
        return results

    # 先去重：KB 已有的就不必查索引、也不必下載
    candidates = []
    for c in all_cands:
        if _already_in_kb(c, kb_raw_dir, naming_config):
            by_doi[c["doi"]].reason = "KB 已有這篇，跳過"
        else:
            candidates.append(c)
    skipped = len(all_cands) - len(candidates)
    if skipped:
        logger.info(f"  去重：{skipped} 篇 KB 已有，跳過")
    if not candidates:
        return results

    sess = _session(email)
    logger.info(f"OA 全文階梯：{len(candidates)} 篇候選")
    resolved = resolve_pdf_urls(sess, candidates, email)

    hit = 0
    for c in candidates:
        r = by_doi[c["doi"]]
        got = resolved.get(c["doi"])
        if not got:
            r.reason = "三個索引都沒有 OA 全文（多半是付費牆）"
            continue
        r.url, r.source = got

        dest = pdfs_dir / f"{_safe_stem(c.get('journal', ''), c.get('pmid', ''), c['doi'])}.pdf"
        if dest.exists():
            r.reason = "已存在，跳過"
            continue
        r.path = dest

        if dry_run:
            r.ok = True
            r.reason = "(dry-run，未下載)"
            hit += 1
            continue

        ok, why = _download_pdf(sess, r.url, dest, timeout, max_bytes)
        r.ok = ok
        if ok:
            hit += 1
            logger.info(f"  ✓ [{r.source}] {dest.name} ← {c.get('title','')[:50]}")
        else:
            r.path = None
            r.reason = why
            logger.warning(f"  ✗ {c['doi']}（{r.source}）：{why}")
        time.sleep(0.5)

    logger.info(f"OA 全文：{hit}/{len(candidates)} 篇{'（dry-run）' if dry_run else ' 已下載'}")
    return results
