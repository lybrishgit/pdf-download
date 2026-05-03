# pdf-download

把 11 本醫學期刊每期 TOC 的 abstract 自動抓下來變成 `.md` + `.html`，配合既有 KnowledgeBase 系統把 PDF 改名後送進 `00-Raw/`。

## 為什麼這樣設計

- **資料源**：[PubMed E-utilities API](https://www.ncbi.nlm.nih.gov/books/NBK25501/)（不是各期刊網站，因為 7/11 已被 Cloudflare 擋）
- **延遲**：PubMed 索引滯後 1–3 天，剛好配合每週節奏
- **跨機器**：`.md` 給 Mac/Obsidian 看，`.html` 給醫院 PC 瀏覽器點 PDF 連結，靠 GDrive 同步
- **AI 評析**：每篇 abstract 用 Claude Sonnet 4.6 寫 100–150 字評析，給 ⭐ 1–5 重要性 + 必讀/瀏覽/跳過 建議
- **文章過濾**：只收 original article / case study / clinical practice / review；排除 perspective / correspondence / editorial

## 整體流程

```
[Mac，VPN ON]
  pdf-download fetch                      ← 抓所有啟用的期刊 TOC
  → GDrive/Journals-Inbox/<date>/{md, html}
  → GDrive 自動同步到醫院 PC

[醫院 PC，已登入內網]
  打開 INDEX.html 看摘要
  → 點 PDF 連結下載到 GDrive/Journals-Inbox/<date>/_pdfs/
  → 自動同步回 Mac

[Mac]
  pdf-download organize                   ← (TODO，尚未實作)
  → PDFs 改名 + 搬到 KB 的 00-Raw/
  → KB 既有 run_daily.py 接手
```

## 安裝

```bash
cd ~/lybrish_claude/sandbox/pdf-download
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Anthropic API Key 設定

把 KB-Scripts 的 API key 複製過來：

```bash
grep ANTHROPIC_API_KEY /Users/lybrish-mini/lybrish_claude/KnowledgeBase-Scripts/.env > .env
```

或手動建立 `.env` 寫入：
```
ANTHROPIC_API_KEY=sk-ant-...
```

### macOS 權限設定（很重要）

預設 macOS 不讓 Python 寫入 `~/Library/CloudStorage/`，會 PermissionError。

打開 **設定 → 隱私與安全性 → 完整磁碟取用權**，把 **Terminal**（或你用的 iTerm/Warp）加進去並打勾，重開 Terminal。

如果不想開那麼大權限，可以把 `config.yaml` 的 `inbox_root` 改到 `~/Documents/Journals-Inbox`，再用 GDrive 桌面 client 設定該資料夾為同步資料夾。

## 快速使用

```bash
# 看支援哪些期刊
python -m pdf_download.cli list-journals

# 抓所有啟用的（config.yaml 預設 11 本全開）
python -m pdf_download.cli fetch

# 只抓 NEJM
python -m pdf_download.cli fetch nejm

# 強制重抓（即使這期已抓過）
python -m pdf_download.cli fetch --force

# 跳過 AI 評析（只抓 abstract，不叫 Claude API）
python -m pdf_download.cli fetch --no-analyze

# 強制重跑 AI 評析（cache 失效）
python -m pdf_download.cli fetch --reanalyze

# debug 模式
python -m pdf_download.cli -v fetch
```

## AI 評析

每篇 abstract 自動送 Claude Sonnet 4.6，產生：

- **⭐ 1–5 重要性評分**（依新意、樣本數、effect size 與你專業相關性）
- **必讀 / 瀏覽 / 跳過** 三檔建議
- **100–150 字評析**：一句結論、方法學體質、對你專業啟示

評析以 Obsidian callout 顯示在 abstract **上方**：
- `[!danger]` 紅色 = 必讀
- `[!tip]` 黃色 = 瀏覽
- `[!note]` 灰色 = 跳過

HTML 版同樣三色 callout，並有「只看必讀」過濾按鈕。

### 成本

每篇 ~$0.005（Sonnet 4.6 input 500 + output 200 tokens）。
11 本一次 fetch 約 60–120 篇 → **單次 $0.30–0.60**（NT$10–20）。
Cache 以 PMID 為 key，重跑同一期不會再叫 API。

要省錢可以改用 Haiku 4.5（成本 1/5、品質可能略差）：

```yaml
# config.yaml
ai_analysis:
  model: claude-haiku-4-5-20251001
```

### Prompt 客製

評析 prompt 在 `prompts/abstract_review.txt`，純文字檔，可以直接改。
改完下次 fetch 自動使用新 prompt（cache 會失效，第一次重跑要錢）。

## 輸出範例

```
~/Library/CloudStorage/GoogleDrive-lybrish@gmail.com/My Drive/Journals-Inbox/
└── 2026-05-03/                ← 你執行 fetch 那天的日期
    ├── INDEX.md               ← Obsidian 看（11 本總覽）
    ├── INDEX.html             ← 醫院 PC 看
    ├── NEJM-2026-04-30.md     ← Obsidian 勾選 ☑
    ├── NEJM-2026-04-30.html   ← 醫院 PC 點 PDF
    ├── JAMA-2026-05-01.md
    ├── ... (其他 9 本)
    └── _pdfs/                 ← 下載的 PDF 丟這
```

## 設定

主要設定在 `config.yaml`：

| 鍵 | 說明 |
|--|--|
| `inbox_root` | GDrive Journals-Inbox 路徑 |
| `kb_raw_dir` | KB 00-Raw 路徑（organize 用） |
| `state_file` | 記錄各期刊抓到哪期的 JSON |
| `journals_enabled` | 預設要跑哪些期刊 |
| `naming.max_title_chars` | PDF 檔名標題部分最大字元數（預設 35） |

### NCBI API Key（可選）

無 key 也可用，但 rate limit 較嚴（3 req/sec）。
申請：https://www.ncbi.nlm.nih.gov/account/settings/
設定：在專案根目錄 `.env` 寫 `NCBI_API_KEY=xxx`，或 `export NCBI_API_KEY=xxx`。

## 加新期刊

不需要寫 Python，只要：

1. 查 PubMed ISO Abbreviation（從一篇該期刊文章的 efetch XML 看 `<ISOAbbreviation>` 標籤）
2. 在 `pdf_download/journals/registry.py` 加一筆 `JournalConfig`
3. 在 `config.yaml` 的 `journals_enabled` 加 slug

例如要加 Lancet Respiratory Medicine：

```python
"lancetrespir": JournalConfig(
    slug="lancetrespir",
    iso_abbrev="Lancet Respir Med",
    abbrev="LancetRespir",
    full_name="The Lancet Respiratory Medicine",
    pdf_url="https://www.thelancet.com/action/showPdf?pii={pii}",
    article_url="https://doi.org/{doi}",
    cadence="monthly",
),
```

## 文章類型過濾

只收這些（PubMed PublicationType 比對）：
- Original article（RCT, Clinical Trial, Observational Study, Multicenter Study, ...）
- Case study（Case Reports）
- Clinical practice（Practice Guideline, Guideline）
- Review（Review, Meta-Analysis, Systematic Review）

排除這些：
- Perspective / Comment / Letter / Editorial / News / Personal Narrative

NEJM 特殊處理（PubMed 只標 "Journal Article" 時靠 DOI 模式判斷）：
- ✅ NEJMoa（Original Article）、NEJMra（Review）、NEJMcp（Clinical Practice）、NEJMcpc（Case Records）、NEJMcps（Clinical Problem-Solving）
- ❌ NEJMp（Perspective）、NEJMc 後接數字（Correspondence）、NEJMe（Editorial）、NEJMicm（Images）

## 一鍵 organize（PDF-Organize.command）

雙擊 `~/Applications/PDF-Organize.command`：
- Terminal 跳出來、跑 organize、跑完按任意鍵關閉
- 右上角會跳 macOS 通知顯示成果

啟動方式：
| 方式 | 怎麼做 |
|--|--|
| Spotlight | `Cmd+Space` → 打 `PDF-Organize` → Enter |
| Dock | 把 `~/Applications/PDF-Organize.command` 拖到 Dock 右側（檔案區）後點圖示 |
| Finder | `~/Applications/PDF-Organize.command` 雙擊 |

通知種類：
- ✓ 完成 — N 篇全部進 KB
- ✓ 部分完成 — X 篇進 KB / Y 篇留在 _pdfs/
- ✓ _pdfs/ 是空的
- ❌ 失敗 — 請看 Terminal 輸出

## 自動排程（每週日 16:00 自動跑）

```bash
bash scripts/install_schedule.sh
```

這個 script 會：
1. **預檢**：先試寫 `inbox_root`，如果沒權限會清楚告訴你怎麼解
2. 安裝 launchd plist 到 `~/Library/LaunchAgents/`
3. 註冊到 launchd（每週日 16:00 觸發）

跑完每次 fetch 會用 macOS 通知顯示「N 篇已就緒、X 篇必讀」。log 統一在 `~/Library/Logs/pdf-download/`。

### 排程相關指令

```bash
# 立刻測試一次（不等到週日）
launchctl kickstart gui/$(id -u)/com.lybrish.pdf-download

# 看下次觸發時間
launchctl print gui/$(id -u)/com.lybrish.pdf-download | grep "next fire"

# 移除排程
bash scripts/uninstall_schedule.sh
```

### macOS 權限設定（重要）

預設 macOS 不讓 Python/launchd 寫入 `~/Library/CloudStorage/`（GDrive/iCloud），會 PermissionError。

**解法 A（推薦）：給 Terminal Full Disk Access**

1. 系統設定 → 隱私與安全性 → **完整磁碟取用權**
2. 加入 **Terminal.app**（或你用的 iTerm/Warp）並打勾
3. 重開 Terminal

**解法 B：改用一般資料夾**

編輯 `config.yaml`：
```yaml
inbox_root: ~/Documents/Journals-Inbox
```
然後在 GDrive 桌面 client → 設定 → 「從電腦同步資料夾」加入 `~/Documents/Journals-Inbox`。

### 排程注意事項

- launchd 只在 Mac **開機 + 沒睡眠** 時才會跑；如果週日下午 Mac 在睡眠，launchd 會在 Mac 醒來後立刻補跑（一次而已，不會多跑）
- fetch 不需要 VPN（PubMed 跟 Anthropic 都是公網），所以排程跑沒問題
- 失敗時 macOS 通知會顯示「失敗，請查看 log」

## TODO

- [ ] `organize` 指令：偵測 `_pdfs/` → 比對 DOI → 套用 skill 命名規則 → 搬到 `00-Raw/`
- [ ] macOS Shortcut（捷徑 App）整合，雙擊一鍵跑 organize
- [ ] 測試：寫 unit tests 覆蓋 PubMed XML 解析
- [ ] 微調：某些期刊（如 AJRCCM）目前抓到較舊的一期，可能要加「指定 vol/iss」選項
