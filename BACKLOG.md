# pdf-download Backlog

集中放「想到再翻」的點子，**全部都還在「評估是否做」階段**，不是 todo list。動工前要先評估每條的 ROI 與風險。

優先級會浮動，定期 review。

---

## 🟡 中（評估中）

### unpaywall API 整合，fetch 完自動下載 OA 全文

**想做的理由**
- weekly 11 本期刊中 OA 比例不低（Thorax / ERJ / 部分 BMJ / 部分 ICM）
- 目前要手動點 PDF 連結 → 自動化估計能省 30-50% 手動下載時間
- 沒有法律 / 政策風險（OA 本來就免費可下載）

**大概怎麼做**
- 抓到 abstract 時順手 query unpaywall（`GET api.unpaywall.org/v2/{DOI}?email=...`）
- 若 `is_oa: true` + `best_oa_location.url_for_pdf` 有值 → 直接下載到 `_pdfs/`
- 命名套 `naming.py`，下次 organize 看到就收

**評估要點**
- 確認 unpaywall rate limit（要不要註冊、能否承受 11 本 × 數十篇）
- 失敗策略：unpaywall 拿不到 / 下載失敗 → 略過、不影響 fetch 主流程
- 是否值得做，看你「OA 篇數佔比 × 手動點的痛感」

---

## 🟢 低（待評估）

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

**最後更新**：2026-05-25
