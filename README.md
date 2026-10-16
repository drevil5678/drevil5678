# 楊海分站賽：AI 協作圍棋賽務系統

依主辦方提供的「115 年第七屆火星人試驗楊海分站賽章程」建立。活動時間為 **2026-10-17 13:00–18:00，12:30 開始檢錄，Asia/Taipei**。主辦方在本次對話指定判負門檻 **40.00%**，取代文件中的 49%／48% 矛盾。

目前版本為 **0.3.0 區域網路網頁版**。50 台現場電腦、正式 GPU／模型、場地網路尚未驗證；不可把雲端煙霧測試當作正式賽驗收。

## 已提供功能

- 瀏覽器 9／13／19 路棋盤、兩位隊員共用一個指定點棋手操作席、觀戰與 SGF 棋譜下載。
- 主辦方區網部署頁：自動列出連線中的區網 IPv4，複製／列印參賽網址，顯示防火牆及網路排查指引；選手只需瀏覽器。Windows 用 `Start-LAN.cmd` 一鍵啟動。
- 報名時產生固定隊伍點棋碼，可賽前列印制發、跨輪沿用、重新制發；舊版資料升級保留既有比賽和原操作碼相容性。公開頁與賽務匯出不含碼。
- 隊伍紀錄頁顯示當前對戰、歷史結果、SGF 與已排定的下一場；瑞士制未配對時顯示等待，不猜測對手。公開使用說明只保留選手資訊。
- `Start-Practice.cmd` 啟動離線同機雙人練習：本機 AI 提示、可悔棋與 SGF 匯出，無計時及正式積分，不使用正式賽資料庫。
- 真實 KataGo 分析，以輪到行棋方觀點提供勝率；依**勝率**排序，不以 KataGo 搜尋次序代替勝率。伺服器拒絕最高勝率落點，也允許建議以外的合法著。
- 伺服器管理每方 10 分鐘、不增秒；每場開賽前選擇自備設備者改為 5 分鐘。瀏覽器重新整理不停止計時。
- 第 61 手起，最佳合法候選（AI 首選以外的次佳）原始勝率連續三個**自身行棋回合 ≤ 0.40** 判負；高於 0.40 即歸零。黑白分開計算。
- 最多 32 隊、每隊兩人、CSV 批次匯入、資格檢查及檢錄。隊員名單更換會移至最後報名順位。
- 六輪瑞士賽：盡量匹配接近積分並避免重複對手、兼顧黑白平衡。勝分 1／1.25，敗分 0；排序依積分、對手積分總和、報名順位。
- 前八種子 1–8、4–5、2–7、3–6；八強與四強單場，冠軍戰三戰兩勝，冠軍戰逐局交換黑白。
- 裁判逐桌／全場暫停、逐桌恢復、重賽及裁定；重賽前保存原棋譜和時鐘；已完成結果禁止覆寫。
- SQLite 持久化、每秒保存運作中的時鐘、重啟自動暫停、裁判與 AI 判定紀錄、資料庫備份、賽務 JSON 匯出。
- 賽事結束後計算名次、隊長積分、可分配積分、冠軍 NT$20,000 與已檢錄隊伍的出場費。額外積分由主辦方分配，系統不自動代分或付款。

## 開發環境

使用現有 `/workspace/drevil5678` checkout。雲端任務已有獨立環境，不需要建立 Git worktree。

需要 Linux x86_64、AVX2/FMA CPU、Python 3.12 或 3.13 與 `uv`。已測試 Python 3.12.14。前端是原生 JavaScript，不需要 Node 建置；Node／Playwright 僅用於選用瀏覽器測試。

```bash
cd /workspace/drevil5678
bash scripts/setup.sh
.venv/bin/python -m pytest -q
bash scripts/start.sh
```

安裝使用 `uv.lock` 的鎖定版本。KataGo v1.16.3 Eigen AVX2 官方 AppImage 經 SHA-256 驗證後解壓，無需 FUSE。安裝可重跑，保留已有賽事資料和本機 `analysis.cfg`。

預設安裝 **b6c96 小模型及 128 visits，僅供開發**。binary 的預期摘要來自官方 GitHub release；舊模型沒有發行者公布摘要，程式記錄第一次自官方 HTTPS release 取得的 SHA-256，後續嚴格檢查。未停用 TLS 或摘要驗證。

HTTP 伺服器預設只監聽 loopback，開啟管理頁前由主機管理員在本機讀取 `.runtime/admin-token` 並安全交付管理員。該檔為隨機生成、權限 0600，不應貼到聊天室、Git、公開文件或螢幕。選手操作碼由管理頁列印本輪操作單，僅發給指定點棋手。頁面登入只留在該瀏覽器分頁工作階段。

## 驗證

```bash
# 規則、權限、配對、積分、備份與恢復測試；測試替身只存在於 tests/
.venv/bin/python -m pytest -q

# 真實 KataGo：獨立臨時賽事，不污染實際名單
.venv/bin/python scripts/smoke.py --teams 32 --board 19 --visits 128

# 雲端映像已提供 Playwright 與 /usr/bin/chromium 時可執行
node scripts/browser-smoke.cjs

# 主機真實區網 IP：獨立資料、16 桌、50 個模擬客戶端
.venv/bin/python scripts/lan_smoke.py

# 離線單機雙人：真實 AI、黑白輪流、悔棋、棋譜與正式資料隔離
.venv/bin/python scripts/practice_smoke.py
```

目前已通過 38 個 pytest 測試與 25 個瀏覽器操作檢查（沒有 JavaScript 錯誤），包含隊伍碼跨輪／重啟沿用、重新制發撤銷舊碼、歷史與未知瑞士配對、選手說明、離線雙人操作、悔棋與棋譜，以及 KataGo 請求實際包含設定的貼目。另有真實 AI 的區網與單機檢查；`.runtime/lan-smoke-report.json` 與 `.runtime/practice-smoke-report.json` 是隔離測試資料，不代表 50 台現場電腦已驗證。

## 50 台電腦與正式活動

區網啟動與網址分享見 [docs/區域網路部署.md](docs/區域網路部署.md)，當日賽務見 [docs/活動部署.md](docs/活動部署.md)。建議 32 台隊伍操作席、1 台 GPU 主機、2 台裁判、1 台排行榜、14 台備用／工作席，合計 50 台。若 GPU 主機另有提供，則多一台備用。每隊兩人共用該队的操作席，不需要為 64 名選手各準備一台電腦。

正式模型、binary 和 GPU 設定可透過 `KATAGO_BIN`、`KATAGO_MODEL`、`KATAGO_CONFIG` 指定；以 `AI_CONCURRENCY` 配合 `numAnalysisThreads`。必須保持 `reportAnalysisWinratesAs = SIDETOMOVE`。`config/analysis-gpu.example.cfg` 是起始範例，尚未在 GPU 上實測。

## 需公告／尚未整合的事項

- 暫採建議的 19 路、黑貼 7.5 目、中國位置超劫規則，可在首輪前調整。正式開賽須由主辦方在管理頁確認規則。
- 章程未定義輪空、認輸、雙方停一手、和局的賽事積分，以及連續低勝率判定之前正常終局的裁定方式。目前不自動處理輪空與和局；雙方停一手會暫停，裁判依主辦方公告選勝隊及 1／1.25 積分。建議先公告後再使用裁定功能。
- AI 分析及排隊期間雙方時鐘暫停；提示到達後才啟動該方時鐘。這是系統異常／等待的處理政策，需在賽前向選手說明。等候時仍可觀察棋盤，現場須限制分析延遲以保持公平。
- AI 首選指該次官方分析回傳且經本機合法性過濾的候選中勝率最高者，並非窮舉所有合法著。完全同勝率時按 visits 較多、座標字典序排序，只禁一個首選；這些細節需納入競賽介面的公告。
- 已加入 Google 表單匯出的 CSV 匯入流程；尚未串接 Google 表單帳號、報名成功郵件、Facebook 公告或金流。此系統不會代你發送訊息。
- 選手姓名與火星積分只顯示給管理員，公開頁只顯示隊名與成績。JSON 匯出移除操作碼；完整資料庫備份仍含操作碼，需安全保管。
- 程式已提交並推送至 `codex/yanghai-local-test`；Windows runner 已完成測試、打包與真實 AI 診斷。雲端環境設定草稿尚未發布，亦未驗證新任務的還原。

## 本地免安裝執行檔

現場伺服器可使用免安裝資料夾版，其他電腦仍用瀏覽器連線。雙擊啟動器、獨立資料目錄、10 分鐘自動備份、啟動前 WAL 完整備份和重複啟動保護已加入。詳見 [docs/本地執行檔.md](docs/本地執行檔.md)。

Windows 使用者先登入 GitHub，從 [Build Windows test package → 最新成功紀錄 → Artifacts](https://github.com/drevil5678/drevil5678/actions/workflows/windows-build.yml) 下載 **`yanghai-windows-lan-v0.3.1`**。先解壓附件 ZIP，再完整解壓內層程式 ZIP。正式區網用 **`Start-LAN.cmd`**，離線雙人練習用 **`Start-Practice.cmd`**。Windows 建置流程須通過正式 LAN 與獨立練習檢查才提供附件；流程見 [主辦操作補充](docs/主辦操作補充.md) 及 [參賽者說明](docs/參賽者說明.md)。

```bash
uv sync --locked --group build
.venv/bin/python scripts/build_portable.py --output dist/yanghai-linux-x64-new
```

Windows 在 Windows x64 建置主機執行 `scripts/build-windows.cmd`；Linux 套件不能當作 Windows exe。發行包不包含賽事資料或金鑰；更新時保留現場 `tournament.json` 與 `data/`。
