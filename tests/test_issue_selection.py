"""選期邏輯的單元測試（不打網路：把 PubMed 回應換成假的 XML）。

跑法：cd 專案根目錄 → PYTHONPATH=. python tests/test_issue_selection.py

守的是 2026-09-10 修的那個坑：
- 線上先行（aheadofprint、卷期皆空）不能被當成一期
- 沒期號的堆 issue_id 不能全撞在一起
- BMJ（連續出版）按 ISO 週當一期，進行中的那週不抓
- fetch_issue("170/2") / ("2026-W28") 能抓指定的一期
"""
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf_download.journals.base import IssueInfo  # noqa: E402
from pdf_download.journals.pubmed import PubMedFetcher  # noqa: E402
from pdf_download.journals.registry import JOURNALS  # noqa: E402


def _article_xml(pmid, doi, vol, iss, pub_date, status):
    y, m, d = pub_date.split("-")
    vol_x = f"<Volume>{vol}</Volume>" if vol else ""
    iss_x = f"<Issue>{iss}</Issue>" if iss else ""
    return f"""
<PubmedArticle>
  <MedlineCitation><PMID>{pmid}</PMID>
    <Article>
      <Journal><JournalIssue>{vol_x}{iss_x}
        <PubDate><Year>{y}</Year><Month>{m}</Month><Day>{d}</Day></PubDate>
      </JournalIssue></Journal>
      <ArticleTitle>Paper {pmid}.</ArticleTitle>
      <ELocationID EIdType="doi">{doi}</ELocationID>
      <Abstract><AbstractText>Some abstract text.</AbstractText></Abstract>
      <AuthorList><Author><LastName>Chang</LastName><Initials>FK</Initials></Author></AuthorList>
      <PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList>
    </Article>
  </MedlineCitation>
  <PubmedData><PublicationStatus>{status}</PublicationStatus></PubmedData>
</PubmedArticle>"""


class _FakeResp:
    def __init__(self, content=b"", js=None):
        self.content = content
        self._js = js or {}

    def json(self):
        return self._js


def _fetcher_with(slug, rows):
    """rows: [(vol, iss, pub_date, status), ...] → fetcher，esearch/efetch 都回這批。"""
    f = PubMedFetcher(JOURNALS[slug])
    pmids = [str(1000 + i) for i in range(len(rows))]
    xml = "<PubmedArticleSet>" + "".join(
        _article_xml(p, f"10.1000/{slug}.{p}", *r) for p, r in zip(pmids, rows)
    ) + "</PubmedArticleSet>"
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append((url, params))
        if "esearch" in url:
            return _FakeResp(js={"esearchresult": {"idlist": pmids}})
        return _FakeResp(content=xml.encode())

    f._get = fake_get
    f._calls = calls
    return f


class IssueIdTest(unittest.TestCase):
    def _issue(self, **kw):
        base = dict(journal_slug="ccm", journal_full="x", journal_abbrev="CCM",
                    volume="", issue="", publication_date="2026-09-07", issue_url="")
        base.update(kw)
        return IssueInfo(**base)

    def test_normal_issue_unchanged(self):
        self.assertEqual(self._issue(volume="54", issue="9").issue_id, "ccm:vol54:iss9")

    def test_empty_issue_gets_bucket(self):
        a = self._issue(bucket="2026-09-07")
        b = self._issue(publication_date="2026-08-26", bucket="2026-08-26")
        self.assertNotEqual(a.issue_id, b.issue_id)
        self.assertEqual(a.issue_id, "ccm:vol:iss:2026-09-07")

    def test_bucket_ignored_when_issue_present(self):
        self.assertEqual(self._issue(volume="54", issue="9", bucket="zzz").issue_id, "ccm:vol54:iss9")


class SelectionTest(unittest.TestCase):
    def test_ahead_of_print_does_not_beat_real_issue(self):
        # 真實案例（CCM 2026-09-10）：9/07 有 4 篇線上先行，9/01 出刊的 vol54 iss9 有 25 篇
        rows = [("", "", "2026-09-07", "aheadofprint")] * 4 + \
               [("54", "9", "2026-09-01", "ppublish")] * 25 + \
               [("", "", "2026-08-12", "aheadofprint")] * 5
        issue = _fetcher_with("ccm", rows).fetch_current_issue()
        self.assertEqual((issue.volume, issue.issue), ("54", "9"))
        self.assertEqual(len(issue.articles), 25)
        self.assertEqual(issue.issue_id, "ccm:vol54:iss9")

    def test_only_ahead_of_print_raises(self):
        rows = [("", "", "2026-09-07", "aheadofprint")] * 6
        with self.assertRaises(RuntimeError):
            _fetcher_with("ccm", rows).fetch_current_issue()

    def test_ppublish_without_issue_number_gets_date_bucket(self):
        # 沒期號但已排期（罕見，如增刊）：不能撞 id
        rows = [("394", "", "2026-08-19", "ppublish")] * 3
        issue = _fetcher_with("chest", rows).fetch_current_issue()
        self.assertEqual(issue.issue_id, "chest:vol394:iss:2026-08-19")


class ContinuousBmjTest(unittest.TestCase):
    def setUp(self):
        self.today = date.today()
        self.this_monday = self.today - timedelta(days=self.today.weekday())
        self.last_monday = self.this_monday - timedelta(days=7)

    def test_groups_by_iso_week_and_skips_running_week(self):
        lm = self.last_monday
        rows = [("394", "", (self.this_monday + timedelta(days=i)).isoformat(), "epublish")
                for i in range(0, min(3, (self.today - self.this_monday).days + 1))]  # 本週（進行中）
        rows += [("394", "", (lm + timedelta(days=i)).isoformat(), "epublish") for i in (0, 2, 4, 6)]
        rows += [("394", "", (lm - timedelta(days=3)).isoformat(), "epublish")] * 3  # 上上週
        issue = _fetcher_with("bmj", rows).fetch_current_issue()
        self.assertEqual(issue.publication_date, lm.isoformat())   # 出刊日＝該週星期一
        self.assertEqual(len(issue.articles), 4)
        y, w, _ = lm.isocalendar()
        self.assertEqual(issue.issue_id, f"bmj:vol394:iss:{y}-W{w:02d}")
        self.assertEqual(issue.issue, "")                             # 期號欄位維持空白，版面不變

    def test_latest_week_wins_even_with_one_article(self):
        # BMJ 過濾後每週常只有 1–2 篇：最新已結束的週只有 1 篇，也要贏過 5 篇的舊週
        lm = self.last_monday
        rows = [("394", "", lm.isoformat(), "epublish")]
        rows += [("394", "", (lm - timedelta(days=7)).isoformat(), "epublish")] * 5
        issue = _fetcher_with("bmj", rows).fetch_current_issue()
        self.assertEqual(issue.publication_date, lm.isoformat())
        self.assertEqual(len(issue.articles), 1)

    def test_two_weeks_have_different_ids(self):
        f = PubMedFetcher(JOURNALS["bmj"])
        a = f._make_issue("394", "", "2026-08-31", [])
        b = f._make_issue("394", "", "2026-09-07", [])
        self.assertNotEqual(a.issue_id, b.issue_id)


class FetchIssueTest(unittest.TestCase):
    def test_volume_issue_selector(self):
        rows = [("170", "2", "2026-08-01", "ppublish")] * 5 + [("170", "3", "2026-09-01", "ppublish")] * 4
        f = _fetcher_with("chest", rows)
        issue = f.fetch_issue("170/2")
        self.assertEqual(len(issue.articles), 5)
        self.assertEqual(issue.issue_id, "chest:vol170:iss2")
        term = f._calls[0][1]["term"]
        self.assertIn("170[Volume]", term)
        self.assertIn("2[Issue]", term)

    def test_week_selector_for_bmj(self):
        monday = date.fromisocalendar(2026, 28, 1)
        rows = [("394", "", (monday + timedelta(days=i)).isoformat(), "epublish") for i in (1, 3, 5)]
        rows += [("394", "", (monday + timedelta(days=8)).isoformat(), "epublish")]  # 下一週，不該混進來
        f = _fetcher_with("bmj", rows)
        issue = f.fetch_issue("2026-W28")
        self.assertEqual(len(issue.articles), 3)
        self.assertEqual(issue.issue_id, "bmj:vol394:iss:2026-W28")
        self.assertIn("[PDAT]", f._calls[0][1]["term"])

    def test_bad_selector(self):
        f = _fetcher_with("chest", [])
        with self.assertRaises(ValueError):
            f.fetch_issue("2026-W28")
        with self.assertRaises(ValueError):
            _fetcher_with("bmj", []).fetch_issue("394/1")

    def test_missing_issue_raises(self):
        f = _fetcher_with("chest", [("170", "3", "2026-09-01", "ppublish")] * 3)
        with self.assertRaises(RuntimeError):
            f.fetch_issue("169/6")


if __name__ == "__main__":
    unittest.main(verbosity=1)
