"""CLI 進入點。

用法：
    pdf-download fetch              # 抓 config 內所有啟用的期刊（含 AI 評析）
    pdf-download fetch nejm jama    # 只抓指定的
    pdf-download fetch --force      # 略過 state 檢查，重抓
    pdf-download fetch --no-analyze # 抓 abstract 但不跑 AI 評析
    pdf-download fetch --reanalyze  # 強制重跑 AI 評析（cache 失效）
    pdf-download list-journals      # 看目前支援哪些
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config.yaml"

# 在 import 業務模組之前先 load .env，確保 NCBI_API_KEY / ANTHROPIC_API_KEY
# 都被 organize / rename / fetch 三條路徑看得到（不只是 analyzer）
load_dotenv(REPO_ROOT / ".env", override=False)

from pdf_download.analyzer import AbstractAnalyzer, load_prompt_template  # noqa: E402
from pdf_download.fetch import run_fetch  # noqa: E402
from pdf_download.journals import JOURNALS, list_journals  # noqa: E402
from pdf_download.organize import organize_pdfs, write_log  # noqa: E402
from pdf_download.rename import rename_pdfs  # noqa: E402
from pdf_download.state import State  # noqa: E402


def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"❌ 找不到設定檔: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def expand(p: str) -> Path:
    return Path(p).expanduser()


def _macos_notify(title: str, message: str) -> None:
    """送 macOS 原生通知（取代 bash wrapper 的 osascript 呼叫，
    避免 bash 對中文標點的變數展開 bug）。"""
    import subprocess
    safe_title = title.replace('"', '\\"')
    safe_msg = message.replace('"', '\\"')
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "{safe_title}"'],
            capture_output=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        pass


def build_analyzer(config: dict) -> AbstractAnalyzer | None:
    """如果 config 啟用 AI 評析，建立 analyzer；否則回 None。"""
    ai = config.get("ai_analysis", {})
    if not ai.get("enabled", False):
        return None

    prompt_path = REPO_ROOT / ai["prompt_file"]
    if not prompt_path.exists():
        sys.exit(f"❌ 找不到 prompt 檔: {prompt_path}")

    return AbstractAnalyzer(
        model=ai["model"],
        prompt_template=load_prompt_template(prompt_path),
        cache_path=expand(ai["cache_file"]),
        max_tokens=ai.get("max_tokens", 600),
    )


def cmd_fetch(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    if args.journals:
        slugs = args.journals
    else:
        slugs = config.get("journals_enabled", [])

    if not slugs:
        sys.exit("❌ 沒有指定任何期刊（config 內 journals_enabled 是空的，也沒有傳參數）")

    inbox_root = expand(config["inbox_root"])
    state = State(expand(config["state_file"]))

    # AI analyzer
    analyzer = None
    if not args.no_analyze:
        try:
            analyzer = build_analyzer(config)
        except RuntimeError as e:
            print(f"⚠️  AI 評析無法啟用：{e}")
            print("   繼續抓 abstract 但跳過評析。如要關掉提醒，加 --no-analyze。")
    else:
        print("ℹ️  --no-analyze：跳過 AI 評析")

    print(f"📥 抓取期刊：{', '.join(slugs)}")
    print(f"📁 輸出位置：{inbox_root}")
    if analyzer:
        print(f"🤖 AI 評析：{analyzer.model} (cache: {analyzer.cache.path})")
    print()

    summary = run_fetch(
        journal_slugs=slugs,
        inbox_root=inbox_root,
        state=state,
        http_config=config.get("http", {}),
        force=args.force,
        analyzer=analyzer,
        force_reanalyze=args.reanalyze,
    )

    print(f"\n✅ 完成！輸出資料夾：{summary['out_dir']}\n")

    if summary["fetched"]:
        print("成功抓取：")
        for f in summary["fetched"]:
            print(f"  • {f['journal']:12s} {f['publication_date']:12s} "
                  f"{f['article_count']} 篇 ({f['oa_count']} OA)")

    if summary["skipped"]:
        print("\n跳過（已抓過，加 --force 可重抓）：")
        for s in summary["skipped"]:
            print(f"  • {s['journal']:12s} {s['issue_id']}")

    if summary["failed"]:
        print("\n❌ 失敗：")
        for f in summary["failed"]:
            print(f"  • {f['journal']:12s} {f['reason']}")

    # --notify：給 launchd 排程用，跑完發 macOS 通知
    if args.notify:
        n_ok = len(summary["fetched"])
        n_failed = len(summary["failed"])
        n_skipped = len(summary["skipped"])
        total_articles = sum(f["article_count"] for f in summary["fetched"])
        # 計算必讀篇數（從 .md 檔抓）
        try:
            must_read = _count_must_read(summary["out_dir"])
        except Exception:
            must_read = None

        if n_ok == 0 and n_failed == 0:
            if not args.silent_when_empty:
                _macos_notify("pdf-download ✓ 無新內容",
                             f"{n_skipped} 本期刊已抓過")
        elif n_failed == 0:
            msg = f"{total_articles} 篇文章"
            if must_read is not None:
                msg += f"·{must_read} 必讀 ⭐⭐⭐⭐+"
            _macos_notify("pdf-download ✓ 本週摘要已就緒", msg)
        else:
            _macos_notify("pdf-download ⚠️ 部分失敗",
                         f"成功 {n_ok} · 失敗 {n_failed}")

    return 1 if summary["failed"] else 0


def _count_must_read(out_dir: Path) -> int:
    """從輸出 .md 統計必讀篇數。"""
    count = 0
    for md in out_dir.glob("*.md"):
        if md.name == "INDEX.md":
            continue
        try:
            text = md.read_text(encoding="utf-8")
            count += text.count("必讀")
        except OSError:
            pass
    return count


def cmd_organize(args: argparse.Namespace) -> int:
    """掃 _pdfs/，比對 DOI，改名搬到 KB 的 00-Raw/。"""
    config = load_config(args.config)
    inbox_root = expand(config["inbox_root"])
    kb_raw_dir = expand(config["kb_raw_dir"])
    naming_config = config.get("naming", {})
    # extra_copy_dir 是選用：留空字串或不設都當作關閉
    raw_extra = config.get("extra_copy_dir") or ""
    extra_copy_dir = expand(raw_extra) if raw_extra.strip() else None

    print(f"📥 來源: {inbox_root / '_pdfs'}")
    print(f"📤 目標: {kb_raw_dir}")
    if extra_copy_dir:
        print(f"📎 副本: {extra_copy_dir}")
    if args.dry_run:
        print("🧪 DRY RUN — 不會實際搬檔，只列出會做什麼")
    print()

    try:
        results = organize_pdfs(
            inbox_root=inbox_root,
            kb_raw_dir=kb_raw_dir,
            naming_config=naming_config,
            dry_run=args.dry_run,
            online_lookup=not args.no_online_lookup,
            extra_copy_dir=extra_copy_dir,
        )
    except RuntimeError as e:
        sys.exit(f"❌ {e}")

    if not results:
        print("✓ _pdfs/ 沒有 PDF 要處理")
        return 0

    matched = [r for r in results if r.matched]
    unmatched = [r for r in results if not r.matched]

    print(f"📊 處理 {len(results)} 個 PDF · ✅ 成功 {len(matched)} · ⚠️  失敗/略過 {len(unmatched)}")
    print()

    if matched:
        print("成功改名：")
        for r in matched:
            print(f"  {r.source.name}")
            print(f"  → {r.target.name}")
            print(f"     DOI: {r.doi} ({r.extract_method})")
            if r.extra_copy_path:
                print(f"     📎 副本: {r.extra_copy_path}")
            elif r.extra_copy_note:
                print(f"     📎 副本: {r.extra_copy_note}")
            print()

    if unmatched:
        print("沒搬到 00-Raw/ 的：")
        for r in unmatched:
            print(f"  ⚠️  {r.source.name}")
            if r.doi:
                print(f"     DOI: {r.doi}")
            print(f"     原因: {r.reason}")
            print()

    log_path = write_log(results, inbox_root, kb_raw_dir, args.dry_run)
    print(f"📝 詳細紀錄: {log_path}")

    # --notify：給 launchd 排程用，跑完發 macOS 通知
    if args.notify:
        if not results:
            if not args.silent_when_empty:
                _macos_notify("PDF Organize ✓", "_pdfs/ 是空的，沒東西要處理")
        elif not unmatched:
            _macos_notify("PDF Organize ✓ 完成", f"{len(matched)} 篇全部進 KB 的 00-Raw/")
        elif not matched:
            _macos_notify("PDF Organize ⚠️", f"{len(unmatched)} 篇都沒對到 abstracts")
        else:
            _macos_notify("PDF Organize ✓ 部分完成",
                         f"{len(matched)} 篇進 KB · {len(unmatched)} 篇留在 _pdfs/")

    return 0 if not unmatched else (0 if matched else 1)


def cmd_rename(args: argparse.Namespace) -> int:
    """原地改名：給任意路徑的 PDF 套用 KB 命名規則（不搬不複製）。"""
    config = load_config(args.config)
    naming_config = config.get("naming", {})

    paths = [Path(p).expanduser() for p in args.paths]

    mode_label = "🚀 APPLY — 實際改名" if args.apply else "🧪 DRY-RUN — 預覽（加 --apply 才動）"
    print(mode_label)
    print()

    results = rename_pdfs(
        paths=paths,
        naming_config=naming_config,
        apply=args.apply,
        online_lookup=not args.no_online_lookup,
    )

    if not results:
        print("沒有 PDF 可處理（路徑不存在、不是 PDF、或資料夾是空的）")
        return 0

    matched = [r for r in results if r.matched]
    unmatched = [r for r in results if not r.matched]
    will_rename = [r for r in matched if not r.already_correct]
    already_ok = [r for r in matched if r.already_correct]

    print(
        f"📊 共 {len(results)} 個 PDF · "
        f"待改名 {len(will_rename)} · "
        f"已正確 {len(already_ok)} · "
        f"略過 {len(unmatched)}"
    )
    print()

    if will_rename:
        verb = "已改名" if args.apply else "建議改名"
        print(f"{verb}：")
        for r in will_rename:
            actual = r.target.name if r.target else "?"
            tag = " ✓" if (args.apply and r.renamed) else ""
            print(f"  {r.source.name}{tag}")
            print(f"  → {actual}")
            print(f"     DOI: {r.doi} ({r.extract_method})")
            print()

    if already_ok:
        print(f"已是正確檔名（不改）：")
        for r in already_ok:
            print(f"  ✓ {r.source.name}")
        print()

    if unmatched:
        print("略過：")
        for r in unmatched:
            print(f"  ⚠️  {r.source.name}")
            if r.doi:
                print(f"     DOI: {r.doi}")
            print(f"     原因: {r.reason}")
        print()

    if not args.apply and will_rename:
        print("💡 確認沒問題後，加 --apply 旗標重跑就會實際改名")

    return 0 if not unmatched else (0 if matched else 1)


def cmd_list_journals(args: argparse.Namespace) -> int:
    print("目前支援的期刊：")
    for slug, full_name in list_journals():
        print(f"  {slug:12s} {full_name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pdf-download")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help=f"設定檔路徑（預設 {DEFAULT_CONFIG}）")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="顯示 debug log")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="抓 TOC 並產生摘要 .md / .html")
    p_fetch.add_argument("journals", nargs="*", help="期刊代號（不傳則用 config 預設）")
    p_fetch.add_argument("--force", action="store_true",
                         help="略過 state 檢查，重抓最新一期")
    p_fetch.add_argument("--no-analyze", action="store_true",
                         help="跳過 AI 評析，只產 abstract")
    p_fetch.add_argument("--reanalyze", action="store_true",
                         help="強制重跑 AI 評析，忽略 cache")
    p_fetch.add_argument("--notify", action="store_true",
                         help="跑完發 macOS 通知（給 launchd 排程用）")
    p_fetch.add_argument("--silent-when-empty", action="store_true",
                         help="搭配 --notify：沒新內容時不通知")
    p_fetch.set_defaults(func=cmd_fetch)

    p_org = sub.add_parser("organize", help="掃 _pdfs/，改名後搬到 KB 的 00-Raw/")
    p_org.add_argument("--dry-run", action="store_true",
                       help="只顯示會做什麼，不實際搬檔")
    p_org.add_argument("--no-online-lookup", action="store_true",
                       help="關閉 PubMed 線上查 metadata（cache 找不到就放棄）")
    p_org.add_argument("--notify", action="store_true",
                       help="跑完發 macOS 通知（給 launchd 排程用）")
    p_org.add_argument("--silent-when-empty", action="store_true",
                       help="搭配 --notify：_pdfs/ 是空時不通知（避免每日跑很煩）")
    p_org.set_defaults(func=cmd_organize)

    p_rename = sub.add_parser(
        "rename",
        help="原地改名任意路徑的 PDF（用 PubMed metadata，不搬不複製）"
    )
    p_rename.add_argument("paths", nargs="+",
                          help="一個或多個路徑（資料夾或單檔，可混用）")
    p_rename.add_argument("--apply", action="store_true",
                          help="實際改名（預設只 dry-run 預覽）")
    p_rename.add_argument("--no-online-lookup", action="store_true",
                          help="不打 PubMed 線上查（純 DOI 抽取，多半會失敗）")
    p_rename.set_defaults(func=cmd_rename)

    p_list = sub.add_parser("list-journals", help="列出目前支援的期刊")
    p_list.set_defaults(func=cmd_list_journals)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
