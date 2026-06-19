"""每週 fetch 完寄出摘要 email（Gmail SMTP）。

設計取捨：
- 內容直接吃 run_fetch 回傳的 summary，不另外重讀檔案（單一真相、不會跟 .md 不同步）。
- 每本期刊一行統計（篇數 / OA / 必讀數），下面列「必讀」文章的標題＋連結，
  讓 Lybrish 一眼掃完就知道這週要不要進醫院抓全文。
- 純標準庫（smtplib + email），不新增依賴。
- 密碼一律從環境變數讀（.env 的 SMTP_PASSWORD），不寫進 config、不進 git。
  Gmail 開了兩步驗證，必須用「應用程式密碼」而非帳號密碼。
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def _must_read_total(summary: dict) -> int:
    return sum(
        1 for f in summary["fetched"] for a in f.get("articles", [])
        if a.get("action") == "必讀"
    )


def _sorted_articles(journal: dict) -> list:
    """文章排序：AI 星等高的浮上來（必讀通常 4-5 星），同分維持原順序。"""
    arts = journal.get("articles", [])
    return sorted(arts, key=lambda a: a.get("stars", 0), reverse=True)


def build_subject(summary: dict, fetch_date: str) -> str:
    n_journals = len(summary["fetched"])
    total = sum(f["article_count"] for f in summary["fetched"])
    must = _must_read_total(summary)
    return f"📚 本週醫學期刊摘要 · {fetch_date} · {n_journals} 本 {total} 篇 · {must} 必讀"


def _esc(s: str) -> str:
    """最小 HTML escape（標題裡偶爾有 < > &）。"""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_bodies(summary: dict, fetch_date: str) -> Tuple[str, str]:
    """回傳 (text_body, html_body)。"""
    fetched = summary["fetched"]
    skipped = summary.get("skipped", [])
    failed = summary.get("failed", [])

    # ---------- 純文字版（HTML 不支援時的 fallback）----------
    tlines: List[str] = [f"本週醫學期刊摘要 · {fetch_date}", ""]
    for f in fetched:
        n_mr = sum(1 for a in f.get("articles", []) if a.get("action") == "必讀")
        tlines.append(
            f"■ {f['journal']} · {f['publication_date']} · "
            f"{f['article_count']} 篇（{f['oa_count']} OA）· {n_mr} 必讀"
        )
        for a in _sorted_articles(f):
            stars = "⭐" * max(1, min(5, a.get("stars", 0)))
            tags = []
            if a.get("action") == "必讀":
                tags.append("必讀")
            if a.get("is_oa"):
                tags.append("OA")
            tag_str = f" [{'·'.join(tags)}]" if tags else ""
            tlines.append(f"   {stars}{tag_str} {a['title']}")
            if a.get("url"):
                tlines.append(f"       {a['url']}")
        tlines.append("")
    if skipped:
        tlines.append("略過（已抓過）：" + ", ".join(s["journal"] for s in skipped))
    if failed:
        tlines.append("失敗：" + ", ".join(x["journal"] for x in failed))
    tlines.append("")
    tlines.append("（各期完整評析頁見本信 .html 附件，點開即看 AI 星等／評析／abstract）")
    text_body = "\n".join(tlines)

    # ---------- HTML 版（用 inline style，避開各家信箱 client 會吃掉 <style>）----------
    must_total = _must_read_total(summary)
    total = sum(f["article_count"] for f in fetched)
    parts: List[str] = [
        '<div style="font-family:-apple-system,Helvetica,Arial,sans-serif;'
        'max-width:640px;margin:0 auto;color:#1a1a1a;line-height:1.5;">',
        f'<h2 style="margin:0 0 4px;">📚 本週醫學期刊摘要</h2>',
        f'<p style="margin:0 0 16px;color:#666;font-size:13px;">'
        f'{fetch_date} · {len(fetched)} 本期刊 · {total} 篇 · '
        f'<strong style="color:#c0392b;">{must_total} 篇必讀</strong></p>',
    ]
    oa_badge = ('<span style="display:inline-block;background:#1a8a3a;color:#fff;'
                'font-size:10px;font-weight:600;padding:1px 6px;border-radius:4px;'
                'margin-left:6px;vertical-align:middle;">OA</span>')
    for f in fetched:
        arts = _sorted_articles(f)
        n_mr = sum(1 for a in arts if a.get("action") == "必讀")
        parts.append(
            '<div style="margin:0 0 18px;padding:12px 14px;background:#f7f7f8;'
            'border-radius:8px;">'
        )
        parts.append(
            f'<div style="font-weight:600;font-size:15px;">{_esc(f["journal"])}'
            f'<span style="color:#888;font-weight:400;font-size:13px;"> · '
            f'{f["publication_date"]} · {f["article_count"]} 篇（{f["oa_count"]} OA）· '
            f'{n_mr} 必讀</span></div>'
        )
        if arts:
            parts.append('<ul style="margin:8px 0 0;padding-left:18px;">')
            for a in arts:
                stars = "⭐" * max(1, min(5, a.get("stars", 0)))
                must = a.get("action") == "必讀"
                title = _esc(a["title"])
                url = a.get("url", "")
                if url:
                    title = (f'<a href="{_esc(url)}" '
                             f'style="color:#1a5fb4;text-decoration:none;">{title}</a>')
                # 必讀的標題用粗體強調，OA 掛綠 badge（可直接點 url 連原文）
                if must:
                    title = f'<strong>{title}</strong>'
                badge = oa_badge if a.get("is_oa") else ""
                parts.append(
                    f'<li style="margin:5px 0;font-size:14px;">'
                    f'<span style="font-size:12px;">{stars}</span> {title}{badge}</li>'
                )
            parts.append("</ul>")
        parts.append("</div>")

    if skipped:
        parts.append(
            '<p style="color:#999;font-size:12px;margin:4px 0;">略過（已抓過）：'
            + ", ".join(_esc(s["journal"]) for s in skipped) + "</p>"
        )
    if failed:
        parts.append(
            '<p style="color:#c0392b;font-size:12px;margin:4px 0;">失敗：'
            + ", ".join(_esc(x["journal"]) for x in failed) + "</p>"
        )
    parts.append(
        '<p style="color:#aaa;font-size:11px;margin-top:20px;border-top:1px solid #eee;'
        'padding-top:8px;">pdf-download 自動寄送 · 標題即連結（'
        f'{oa_badge} 為開放取用，點下去直接看全文）· '
        '<strong>各期完整評析頁（AI 星等／評析／abstract）見本信附件 .html，點開即看</strong></p>'
    )
    parts.append("</div>")
    html_body = "\n".join(parts)

    return text_body, html_body


def _attach_html_files(msg: EmailMessage, paths: Sequence[Path]) -> int:
    """把各期評析 .html 掛成附件（self-contained，點開即完整評析頁）。

    回傳實際掛上的份數。讀不到的檔（GDrive 佔位檔 / 路徑不存在）跳過並 warning，
    不影響信件主體。
    """
    attached = 0
    for p in paths:
        p = Path(p)
        if not p.is_file():
            logger.warning(f"  附件略過（檔案不存在）：{p}")
            continue
        try:
            data = p.read_bytes()
        except OSError as e:
            logger.warning(f"  附件讀取失敗，略過：{p}（{e}）")
            continue
        msg.add_attachment(
            data, maintype="text", subtype="html", filename=p.name
        )
        attached += 1
    return attached


def send_report(
    email_config: dict,
    subject: str,
    text_body: str,
    html_body: str,
    attachments: Optional[Sequence[Path]] = None,
) -> None:
    """用 Gmail SMTP（SSL）寄出。密碼從環境變數讀，缺了就明確報錯。

    email_config 來自 config.yaml 的 email 區塊。
    attachments：各期評析 .html 路徑；掛成附件讓使用者點開看完整評析版面。
    """
    if not email_config.get("enabled"):
        raise RuntimeError("config 的 email.enabled 不是 true，跳過寄信")

    pw_env = email_config.get("password_env", "SMTP_PASSWORD")
    password = os.environ.get(pw_env, "").strip()
    if not password:
        raise RuntimeError(
            f"環境變數 {pw_env} 沒設。請在專案根目錄 .env 填 Gmail「應用程式密碼」：\n"
            f"    {pw_env}=xxxxxxxxxxxxxxxx\n"
            "（Google 帳號→安全性→兩步驟驗證→應用程式密碼產生，16 碼）"
        )

    sender = email_config["sender"]
    recipient = email_config["recipient"]
    host = email_config.get("smtp_host", "smtp.gmail.com")
    port = int(email_config.get("smtp_port", 465))

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    if attachments:
        n = _attach_html_files(msg, attachments)
        logger.info(f"  掛上 {n} 份評析頁附件")

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)
    logger.info(f"email 摘要已寄出 → {recipient}")
