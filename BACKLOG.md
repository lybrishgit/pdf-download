# pdf-download Backlog

集中放「想到再翻」的點子，**全部都還在「評估是否做」階段**，不是 todo list。動工前要先評估每條的 ROI 與風險。

優先級會浮動，定期 review。

---

## 🟡 中（評估中）

### email 開評析頁 HTML 不卡卡（尤其手機）

**背景**（2026-06-19 加）
- weekly email（`fetch --email`）現在會把各期評析頁 `issue.html` 當**附件**帶上。
- Mac：雙擊附件 → 預設瀏覽器直接開，完整渲染 ✅
- **痛點 = 手機**：iPhone Mail 點 HTML 附件多半進 Quick Look（半渲染或當純文字），
  要再「分享 → 用 Safari 打開」才完整，多兩個動作不順；Gmail 網頁版也要先下載再開。
- 評析頁本身是自包含的（CSS/JS 內嵌、文章連結絕對網址），所以「能不能開」不是問題，
  問題是**手機上開的動線卡**。

**候選解法**
1. **發佈到網路 + email 放真網址**（最順）：每週把評析頁部署到 Netlify / Cloudflare Pages，
   手機 Safari 一點就開。工作區已有 `md-to-guideline-web`（Netlify）那套可借。
   - 取捨：要設定一次部署 + 自動化每週 deploy；內容（abstract + AI 評析）會上網路
     （abstract 本來就公開、風險低），保險可加不公開網址 / 密碼保護 / basic auth。
2. 附件改成「單一自包含合併 HTML」（5 期併一檔）：少幾個附件，但手機附件動線一樣卡。
3. 維持附件 + 另放網址（兩者都要）。

**評估要點**
- 你到底多常在**手機**上看 weekly email？只在 Mac 看 → 維持附件就好，不用做。
- 若要上網路：選 Netlify 還是 Cloudflare？要不要密碼保護？每週舊頁要保留還是覆蓋？
- 部署步驟接在 `fetch --email` 之後，失敗不可影響 fetch 主流程。

---

### ~~unpaywall API 整合，fetch 完自動下載 OA 全文~~ → ✅ 已做（2026-07-15），但**原本的規劃是錯的**

**已上線**：`fetch --download-oa`（`pdf_download/oa_fetch.py`），週日排程已掛。

**這條為什麼值得留著當教訓**：本條原本寫「query unpaywall → 看 `best_oa_location.url_for_pdf` →
下載」。2026-07-15 用 72 篇真實資料實測，證明**這個做法是啞彈**：

| 路由 | 單獨命中（72 篇） |
|---|---|
| **純 Unpaywall（本條原本的規劃）** | **7/72 ＝ 9.7%** ← 做了等於沒做 |
| Semantic Scholar `openAccessPdf` | 27/72 ＝ 37.5% |
| PMC（有 PMCID → Europe PMC） | 23/72 ＝ 31.9% |
| **三者聯集（實際採用）** | **33/72 ＝ 45.8%** |

主因：Unpaywall 常說「是 OA」卻只給落地頁、`url_for_pdf` 是 None（實測 24 篇這種，
其中 23 篇靠 S2/PMC 救回）。**價值全在「多問幾個索引」，不在 Unpaywall。**

**實作上被實測推翻的另外兩點**（別再走回頭路）：
- **不要用 `is_open_access` 當閘門**：聯集 33 篇 > PubMed 標 OA 的 24 篇，多出的是
  作者手稿 / 典藏版。對全部有 DOI 的篇都走一次階梯。
- **Unpaywall 不是多餘的**：它單獨命中低、且在聯集裡加成為 0，一度以為可以拿掉；
  但實測 S2 會回 429，那時 Unpaywall 是唯一把漏的撈回來的安全網。**留著當 fallback。**

命中集中在重症/胸腔（ICM 3/3、AJRCCM 7/10、CCM 13/24）；NEJM 1/14（付費牆重鎮）。

---

## 🟢 低（待評估）

### organize log 檔名跨日互相覆蓋

**現況**（2026-07-17 發現）
- organize 的 log 寫成 `<最新fetch日期夾>/organize-log_HHMM.md`，檔名只有 `HHMM`。
- 但日期資料夾**每週才換一次**（fetch 週日跑時才建新夾）。
- 後果：每天 03:00 的 organize 都寫 `organize-log_0300.md` 到**同一個週的資料夾** →
  **不同天的同一時刻互相覆蓋**（7/16 03:00 的 log 已被 7/17 03:00 蓋掉）。

**為什麼低**
- `~/Library/Logs/pdf-download/organize-auto-stdout.log` 是累加的，**歷史沒真的丟**，
  要回溯時間軸從那裡撈得到（這次就是這樣還原的）。GDrive 那份 `.md` 只是給人順手看的副本。
- 不影響任何實際處理邏輯。

**修法（很小）**
- log 檔名加日期：`organize-log_YYYY-MM-DD_HHMM.md`。
- 註：`CLAUDE.md` §8a 原本加 HHMM 的用意是「避免同一天多次跑覆蓋」，只想到同一天、
  沒想到「不同天同一時刻」也撞——因為 log 落在「最新 fetch 日期夾」而非「今天的夾」。

---

### EZproxy 下載 CLI（機構訂閱全文）

**想做的理由**
- 補完 `fetch → 全文 → organize` pipeline 中間那段手動下載
- 理論上一行指令給 DOI 就拉到 `_pdfs/`

**不急的理由**
- weekly 5-10 篇全文，醫院 PC 手動點 5 分鐘搞定，ROI 偏低
- 出版商 bulk download 偵測風險真實存在（整個機構 IP 被封）

**評估要點（做之前要確認）**
- 機構 EZproxy domain 規則：登入圖書館看 URL rewriting（`www.nejm.org` → `www-nejm-org.<機構>:<port>`，點變 dash）
- 認證機制：
  - 純帳密 → `requests` + cookie jar 解決
  - SSO（SAML / Shibboleth）→ 要 Playwright headless browser，難度跳一級
- 出版商防 abuse：硬性 rate limit ≤ 1 req / 5 sec、一次最多 N 篇
- 找圖書館員打個招呼，避免被當 abuser

---

### 補 tests/

**現況**：只有 1 個 134 行的 render 測試。

**待覆蓋**
- `extract_doi` 三段 fallback（filename / metadata / first_page）
- `lookup_doi` retry 邏輯（transient 5xx、4xx 不重試）
- `_collect_pdfs` recursive + 跳隱藏目錄
- `_copy_to_unmatched` 三個分支（成功 / 已存在 / dry-run）
- `_resolve_conflict` `_dup` 後綴邏輯

**為什麼低**：5/13 三條 backlog 都靠 smoke test 驗，沒踩到 regression。不痛但不踏實。

---

### `pdf-download status` 指令

**功能想像**
- 看排程健康度（launchctl 狀態 + 上次跑時間）
- `_pdfs/` 待處理數
- 上次 fetch / organize 是否成功

**為什麼低**：兩個排程都靠 `--notify` 推 macOS 通知，平常看通知夠了。要 debug 才會想要這指令。

---

**最後更新**：2026-07-17
