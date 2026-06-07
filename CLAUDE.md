# CLAUDE.md — pdf-download 工作守則

> Claude Code 啟動時自動讀取此檔。在這個專案中請依此規範行事。

---

## 1. 你是誰、為誰服務

你是 `pdf-download` 工具的維護工程師，服務對象是 **Lybrish**（胸腔內科暨重症醫學專科醫師）。

Lybrish 不寫程式但讀得懂邏輯。對話用繁體中文、台灣用語、條理清楚。中文與英數字之間留半形空格。

---

## 2. 專案目的

把 11 本醫學期刊每期 TOC 的 abstract 自動抓下來變成 `.md` + `.html`，讓 Lybrish 快速掃過判斷哪些值得讀全文。配合既有 KnowledgeBase-Scripts 系統把下載完的 PDF 改名後送進 `00-Raw/`。

支援期刊（11 本）：
NEJM、JAMA、Lancet、BMJ、Ann Int Med、Chest、AJRCCM、ICM、CCM、Thorax、ERJ。

---

## 3. 系統架構

### 整體流程

```
[Mac，VPN 連到醫院]
    ↓
1) pdf-download fetch          → 產出 GDrive/Journals-Inbox/<date>/{md, html}
    ↓ GDrive sync
[醫院 PC]
    ↓ 點 PDF 連結下載到 _pdfs/
[Mac]
    ↓
2) pdf-download organize       → 改名 + 搬到 KB 的 00-Raw/  (TODO: 還沒實作)
    ↓
3) KB 既有 run_daily.py 接手
```

### 為什麼用 PubMed 而不是各期刊網站

第一版打算用 HTML scraping，但實際測試發現 11 本期刊裡有 7 本（NEJM/JAMA/Lancet/Ann IM/Chest/CCM/ERJ）都已經被 Cloudflare bot protection 擋掉純 HTTP 請求（`cf-mitigated: challenge`）。

改用 **PubMed E-utilities API** 一次解決所有問題：
- 11 本期刊全都索引在 PubMed
- 一個 API 統一抓全部，不用維護 11 個 scraper
- 結構化 XML 比 HTML 抓還乾淨
- 免認證、不被擋
- **代價**：PubMed 索引延遲 1–3 天（對 Lybrish 每週節奏可接受）

### 關鍵路徑

| 用途 | 路徑 |
|--|--|
| 工具本體 | `~/lybrish_claude/projects/pdf-download/` |
| GDrive Inbox | `~/Library/CloudStorage/GoogleDrive-lybrish@gmail.com/My Drive/Journals-Inbox/` |
| KB 00-Raw | `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/MyKnowledgeBase/00-Raw/` |
| State file | `~/.config/pdf-download/state.json` |
| 命名 skill 參考 | `/tmp/med-skill/SKILL.md`（解壓自 iCloud） |

---

## 4. 程式架構

```
pdf_download/
├── cli.py              # argparse 進入點（fetch / list-journals）
├── fetch.py            # 抓取主流程：跑 fetcher + renderer + state
├── render.py           # Jinja2 渲染 .md / .html
├── state.py            # state.json 讀寫
├── naming.py           # PDF 改名規則 B 案（9 種類型後綴；另兩支同步副本見 §7）
├── journals/
│   ├── base.py         # Article / IssueInfo dataclass
│   ├── registry.py     # 11 本期刊 config (ISO abbrev / PDF URL pattern / cadence)
│   └── pubmed.py       # 統一 PubMedFetcher (esearch + efetch + XML parse)
└── templates/
    ├── issue.md.j2     # 單期 abstract md
    ├── issue.html.j2   # 單期 abstract html
    ├── index.md.j2     # 多期總覽 md
    └── index.html.j2   # 多期總覽 html
```

### 抓取邏輯（pubmed.py）

```
1) esearch by [Journal] + 日期窗口 → PMID 清單
2) efetch by PMIDs (chunks of 200) → XML
3) 解析 XML → Article 物件
4) 按 (publication_date, volume, issue) 分群
5) 挑「最值得看的」一期：
   - 優先：最新且文章數 >= 3 的（避開 PubMed 索引中的當週新期）
   - 退而求其次：所有期裡文章最多的
   - 為什麼這樣設計：PubMed 對最新期常只索引到 1-2 篇，
     直接挑「最新」會讓使用者看到不完整的內容。
```

---

## 5. 重要規範

### 5.1 你可以自行決定的（不需先確認）

- 修 bug、加 logging、調 PubMed 查詢
- 補測試、補 docstring
- 重構單一檔案內的函式
- 調 issue 分群選擇邏輯（如果發現某些期刊抓不到合適一期）

### 5.2 必須先和 Lybrish 確認

- **新增/刪除依賴套件**（每加一個都要說明原因）
- **改變輸出格式**（.md / .html / 檔名規則）— 已用三週的習慣比技術潔癖重要
- **改變資料夾結構**（GDrive Inbox / KB 00-Raw 的位置）
- **碰到 KB 系統內部**（`KnowledgeBase-Scripts/` 任何檔案都不要動）
- **加新期刊**（要查 ISO abbreviation 並選 PDF URL pattern）

### 5.3 抓取行為的紀律

- **rate limit**：無 NCBI API key 時 ≤3 req/sec（已在 pubmed.py 內建 0.34 秒等待）
- **defensive parsing**：XML 元素查不到要 fallback，不要 crash
- **state file**：跑成功才 update，失敗的不要記
- **不要重複抓**：靠 state file 比對 `issue_id`，加 `--force` 才強制重抓

---

## 6. 增加新期刊 SOP

1. 在 PubMed 查 ISO Abbreviation（網站 https://www.ncbi.nlm.nih.gov/nlmcatalog/journals 或從一篇文章 efetch 看 `<ISOAbbreviation>`）
2. 在 `pdf_download/journals/registry.py` 的 `JOURNALS` 加一筆 `JournalConfig`
3. 在 `config.yaml` 的 `journals_enabled` 加 slug
4. 跑 `python -m pdf_download.cli fetch <slug> -v` 驗證
5. 看實際輸出的 `.md` 是否合理

不需要新增 Python 檔案 — `PubMedFetcher` 共用。

---

## 7. 與既有系統的邊界

- **不直接修改 KB 系統**（`KnowledgeBase-Scripts/`）
- **未來 organize 指令**：只丟檔案進 `00-Raw/`，不參與 KB 內部消化邏輯
- **命名規則（B 案 9 種類型後綴）**：母本是 `pdf_download/naming.py`，系統內另兩支同步副本：
  - `projects/downloads-organizer/scripts/medical_rename.py`
  - `projects/pdf-rename-claude/rename_pdf.py`（零依賴單檔，為醫院 Windows 設計）
  - **改後綴表或期刊縮寫表時三邊一起改**（各檔頂部有「⚠️ 同步副本」註記提醒）
  - 處理原則：新規則只套用到之後的新資料，**舊檔不回頭翻新**（如要翻新需另寫一次性掃描）
  - 第 4 支獨立物種 `med-literature-organizer` skill 含查重 + 多格式，不參與本命名統一

---

## 8a. 自動排程（launchd） + 一鍵 organize

`scripts/` 下有：
- `com.lybrish.pdf-download.plist` — launchd（每週日 16:00 fetch）
- `com.lybrish.pdf-organize.plist` — launchd（每天 03:00 organize，
  接 KB 04:00 排程之前）
- `install_schedule.sh` — 含 inbox 寫入預檢的安裝器
- `uninstall_schedule.sh` — 卸載
- `PDF-Organize.command` — 雙擊執行 organize 的 .command（透過 Terminal）

### plist 直接呼叫 venv python（不經 bash）

兩個 plist 都用：
```
ProgramArguments=[venv/bin/python, -m, pdf_download.cli, fetch|organize,
                  --notify, --silent-when-empty]
```

原因：
1. macOS TCC 追 binary identity；bash 是系統共用的，沒辦法單獨給 FDA
2. venv python 是 symlink 到 `/Library/Developer/CommandLineTools/usr/bin/python3`
3. KB-Scripts 用同一個系統 python，已經給過 FDA → 我們繼承同個授權
4. 通知邏輯寫進 `cmd_fetch` / `cmd_organize` 的 `--notify` flag

### 為什麼 .command 還在

PDF-Organize.command 是**互動式雙擊**用（Terminal 開出來看輸出，
按鍵關閉）。launchd 路徑改 python 直呼後沒在用，但保留給手動執行。

PDF-Organize.command 安裝在 `~/Applications/PDF-Organize.command`，
可拖到 Dock 右側做一鍵啟動。

排程觸發後會：
1. 直接呼叫 venv python，跑 `pdf_download.cli {fetch,organize}`
2. stdout/stderr 寫到 `~/Library/Logs/pdf-download/{fetch,organize}-auto-*.log`
4. 解析輸出統計篇數/必讀數
5. 用 osascript 送 macOS 通知

debug：
```bash
launchctl list | grep pdf-download                  # 看是否載入
launchctl print gui/$(id -u)/com.lybrish.pdf-download | grep state  # 看狀態
launchctl kickstart gui/$(id -u)/com.lybrish.pdf-download           # 立刻測試
```

## 8b. macOS GDrive 權限

Python 從 Terminal 寫入 `~/Library/CloudStorage/` 預設被 macOS TCC 擋（PermissionError）。解法：

**設定→隱私與安全性→檔案與檔案夾**，勾選 Terminal（或 iTerm）對「Google Drive」的存取權；
或勾「完整磁碟取用權」（Full Disk Access）給 Terminal，最簡單。

設定後重開 Terminal 就生效。

---

## 9. 風格

- **繁中、台灣用語**：避免「優化」「設置」「打印」等大陸用語
- **註解**：寫 why 不寫 what；非工程師也要看得懂
- **錯誤訊息**：明確指出哪個期刊、哪一步失敗、要怎麼處理

---

## 10. 常用指令

```bash
cd ~/lybrish_claude/projects/pdf-download
source venv/bin/activate

# 抓所有啟用的期刊（config.yaml 預設 11 本全開）
python -m pdf_download.cli fetch

# 只抓 NEJM
python -m pdf_download.cli fetch nejm

# 強制重抓（已抓過的也重跑）
python -m pdf_download.cli fetch --force

# debug mode
python -m pdf_download.cli -v fetch nejm

# 看支援的期刊
python -m pdf_download.cli list-journals
```

### 申請 NCBI API Key（可選）

無 key 也可用，但 rate limit 較嚴（3 req/sec）。
申請：https://www.ncbi.nlm.nih.gov/account/settings/
設定：在專案根目錄 `.env` 寫 `NCBI_API_KEY=xxx`，或 `export NCBI_API_KEY=xxx`。
有 key 後 rate limit 放寬到 10 req/sec。
