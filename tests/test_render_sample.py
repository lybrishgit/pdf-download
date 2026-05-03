"""用假資料測試 renderer，輸出到 /tmp/pdf-download-sample/，可以肉眼檢查格式。

跑法：
    python -m tests.test_render_sample
"""

from datetime import datetime
from pathlib import Path

from pdf_download.journals.base import Article, IssueInfo
from pdf_download.naming import build_pdf_filename
from pdf_download.render import Renderer


def make_sample_issue() -> IssueInfo:
    articles = [
        Article(
            title="Mucosal Vaccination Clears Clostridioides difficile Colonization",
            authors="Smith J, Lee MK, Patel R, Anderson C, et al.",
            doi="10.1056/NEJMoa2401234",
            article_type="Original Article",
            pdf_url="https://www.nejm.org/doi/pdf/10.1056/NEJMoa2401234",
            article_url="https://www.nejm.org/doi/full/10.1056/NEJMoa2401234",
            pages="1567–1578",
            is_open_access=True,
            abstract_sections=[
                ("Background",
                 "Recurrent Clostridioides difficile infection (CDI) remains a major "
                 "clinical challenge with limited preventive strategies. Whether mucosal "
                 "immunity is required for protection has been unclear."),
                ("Methods",
                 "We compared mucosal versus parenteral vaccination in a murine model of "
                 "CDI, evaluating colonization clearance, morbidity, mortality, tissue "
                 "injury, and recurrence."),
                ("Results",
                 "Mucosal vaccination cleared colonization in 92% of treated mice "
                 "(95% CI 84–97), as compared with 18% in parenterally vaccinated mice "
                 "and 12% in controls (P<0.001)."),
                ("Conclusions",
                 "Mucosal vaccination — but not parenteral — achieved durable clearance "
                 "of C. difficile colonization, suggesting a viable path for human CDI "
                 "prevention."),
            ],
        ),
        Article(
            title="Empagliflozin in HFpEF with Mild Cognitive Impairment",
            authors="Anker SD, Butler J, Filippatos G, et al.",
            doi="10.1056/NEJMoa2401456",
            article_type="Original Article",
            pdf_url="https://www.nejm.org/doi/pdf/10.1056/NEJMoa2401456",
            article_url="https://www.nejm.org/doi/full/10.1056/NEJMoa2401456",
            pages="1579–1590",
            is_open_access=False,
            abstract_sections=[
                ("Background",
                 "Sodium–glucose cotransporter 2 inhibitors improve outcomes in heart "
                 "failure with preserved ejection fraction, but cognitive interactions "
                 "are unknown."),
                ("Methods",
                 "Multinational, randomized, double-blind trial of empagliflozin vs "
                 "placebo in 4823 patients with HFpEF and MCI."),
                ("Conclusions",
                 "Empagliflozin reduced HF hospitalizations regardless of cognitive "
                 "status."),
            ],
        ),
        Article(
            title="Acute Respiratory Distress Syndrome — Diagnosis and Management",
            authors="Thompson BT, Chiumello D, Brower RG.",
            doi="10.1056/NEJMra2401789",
            article_type="Review Article",
            pdf_url="https://www.nejm.org/doi/pdf/10.1056/NEJMra2401789",
            article_url="https://www.nejm.org/doi/full/10.1056/NEJMra2401789",
            pages="1601–1612",
            is_open_access=False,
            abstract_sections=[
                ("",
                 "Acute respiratory distress syndrome (ARDS) is characterized by acute "
                 "hypoxemic respiratory failure with bilateral pulmonary infiltrates not "
                 "fully explained by cardiac failure or fluid overload. This review "
                 "summarizes current diagnostic criteria, mechanical ventilation "
                 "strategies, and emerging adjunctive therapies including prone "
                 "positioning, neuromuscular blockade, and ECMO."),
            ],
        ),
    ]

    return IssueInfo(
        journal_slug="nejm",
        journal_full="New England Journal of Medicine",
        journal_abbrev="NEJM",
        volume="392",
        issue="18",
        publication_date="2026-04-30",
        issue_url="https://www.nejm.org/toc/nejm/392/18",
        articles=articles,
    )


def main():
    out_dir = Path("/tmp/pdf-download-sample/2026-05-03")
    out_dir.mkdir(parents=True, exist_ok=True)

    issue = make_sample_issue()
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    fetch_date = datetime.now().strftime("%Y-%m-%d")

    renderer = Renderer()
    md, html = renderer.render_issue(issue, fetched_at, out_dir)
    idx_md, idx_html = renderer.render_index([issue], fetched_at, fetch_date, out_dir)

    print(f"\n📁 樣本輸出於：{out_dir}\n")
    print(f"  {md}")
    print(f"  {html}")
    print(f"  {idx_md}")
    print(f"  {idx_html}")

    print("\n📄 檔名範例（套用 naming.py）：")
    for art in issue.articles:
        fn = build_pdf_filename(
            year=issue.publication_date[:4],
            journal_abbrev=issue.journal_abbrev,
            title=art.title,
            article_type=art.article_type,
        )
        print(f"  {fn}")

    print(f"\n試打開：")
    print(f"  open {html}")
    print(f"  open {idx_html}")


if __name__ == "__main__":
    main()
