# 楊海分站賽：AI 協作圍棋賽務系統

目前版本 **v0.8.0**。依主辦方提供的章程與後續指定實作：18 隊預載、五輪瑞士賽、八強淘汰賽、每局骰子猜先，以及同一網址的單機練習。活動為 **2026-10-17 13:00–18:00，Asia/Taipei**。

新增獨立 **`yanghai-windows-knockout-v0.8.0` 八強單機版**：自行輸入八個隊名，相鄰配對；八強一局定勝負，四強與冠軍戰三戰兩勝。雙擊 **YanghaiKnockout.exe**，不需先打瑞士賽；資料存於 `knockout-data/`，與原賽事及雙隊備用版分開。勝局自動累計並帶入下一階段，詳見[八強淘汰賽單機版](docs/八強淘汰賽單機版.md)。

另提供 **`yanghai-windows-duel-v0.8.0` 單機雙隊 PK 備用包**。解壓後雙擊 **YanghaiDuel.exe**，兩隊共用一台電腦，無需網路或安裝 Python。保留正式猜先、計時與 40% 判負，預設使用 CPU；棋局存到獨立的 `duel-data/`。結果需由主辦在原區網賽局登錄，詳見[單機雙隊備用版](docs/單機雙隊備用版.md)。

## 開始使用

登入 GitHub，從 [Windows 建置頁](https://github.com/drevil5678/drevil5678/actions/workflows/windows-build.yml) 的最新成功紀錄下載 `yanghai-windows-lan-v0.8.0`。解壓附件與內層程式 ZIP，保留全部檔案，雙擊 **Start-LAN.cmd**。主機不需 Python／uv，其他電腦只需瀏覽器。

1. 使用本機 `data/admin-token` 登入主辦管理，在「區域網路開賽」複製參賽網址。
2. 18 隊與固定點棋碼已內建，所有隊伍預設完成資格確認及檢錄。「暫無」是有效姓名，不必補人。無需火星積分。
3. 核對規則並儲存，列印隊伍碼卡，建立第一輪瑞士賽。
4. 主辦一鍵啟用本輪全部桌。每局雙方各擲兩顆骰子，看完棋盤中央動畫後按「確定」確認點數，總和高者選黑白；平手重擲。雙方按「準備好了」後才開始。
5. 五輪完成後建立八強。網站另有淘汰賽對決圖、隊伍紀錄與「練習」。

詳見 [18 隊快速開賽](docs/18隊快速開賽.md)、[區域網路部署](docs/區域網路部署.md)、[主辦操作](docs/主辦操作補充.md) 與 [參賽者說明](docs/參賽者說明.md)。更新時保留 `tournament.json` 和 `data/`，不要用測試資料覆蓋實際賽事。

## 賽制與對弈

- 9／13／19 路棋盤可選，預設 19 路、黑貼 7.5 目、中國位置超劫；每次真實 KataGo 分析均包含貼目，勝率採輪到行棋方觀點。
- AI 候選依原始勝率排序，最高勝率的棋盤落點禁止選擇；其他合法點也可下。相同勝率依 visits 與座標排序。虛手依規則設定開放。
- 顯示目前第幾手。第 61 手起，最佳可選候選勝率連續三個自身回合 ≤ 40.00% 判負，高於門檻歸零；勝方 1.25 分，敗方 0 分。
- 一般對局每方 10 分鐘；自備電子產品方為 5 分鐘。冠軍戰各局每方 15 分鐘，自備電子產品方為 10 分鐘。分析排隊不扣時，重新整理不停止時間。
- 連續雙方虛手即依棋盤現況計分，不移除死子：中國規則算子空，日本規則算空與提子，白方加貼目；勝方 1 分、敗方 0 分。若設定造成恰好同分，暫停交裁判裁定。
- 五輪瑞士賽：第一輪依報名順位相鄰配對。之後依積分、對手積分總和、報名順位排序，從第一隊開始找下方排名最近的未交手隊伍；必要時回退完成整輪配對。找不到不會擅自重複對手。
- 八強種子對決為 1–8、4–5、2–7、3–6；八強及四強單局，冠軍戰三戰兩勝。每局均重新骰子猜先。
- 主辦可指定兩隊建立獨立特別桌；不計瑞士排名或淘汰晉級。裁定勝負可直接選勝隊及分數，不需理由。
- 正式賽持久化至 SQLite，支援暫停、重賽、備份、SGF、歷史紀錄及賽務匯出。重啟保留棋局並暫停；已完成結果不覆寫。

## 同一網址的單機練習

練習預設關閉。主辦在後台「練習開放控制」可隨時開啟／關閉，設定會持久保存。選手須以正式隊伍點棋碼登入，後端核對後才可開桌與操作。關閉時保留棋盤並停止操作與電腦回應，仍可下載 SGF／收桌，重新開放可繼續；暫停期間不計閒置回收時間。

在賽場、積分榜、隊伍紀錄同列選單按「練習」，與正式網站使用同一連接埠，不需再啟動服務。兩人在同一台電腦輪流落子，或一人對電腦；不提供多台電腦連線對打或其他裝置入桌。每個瀏覽器只保留一桌，收桌後才能開新局；練習不計正式成績。

每桌綁定開桌瀏覽器與裝置，開桌後使用原瀏覽器繼續。收桌、逾時或主機重啟會清除練習棋局，請先下載 SGF。練習與正式比賽共用 AI，實際同時運作量須依硬體彩排。`Start-Practice.cmd`／`Start-Practice-LAN.cmd` 為主網站啟動捷徑；舊離線雙人模式仍可使用 `Start-Practice-Offline.cmd`／`--practice`。

## RTX 4050 等 NVIDIA 顯卡

Windows 套件已附 GPU 引擎。先執行 `Check-GPU.cmd` 確認顯卡，停止伺服器後可用 `Benchmark-12-Tables.cmd` 做 12 桌、3 輪真實中盤分析。`Start-LAN.cmd` 自動選 NVIDIA，失敗時明確顯示 CPU；`Start-LAN-GPU.cmd` 要求 GPU 成功才讓 AI 就緒。主辦後台顯示實際引擎、顯卡與排隊數，詳見 [GPU 與 12 桌測速](docs/GPU與12桌測速.md)。模型、visits、隊伍及棋局不因 GPU 模式而重設。

## 開發與檢查

使用現有 checkout，不需 worktree。需要 Python 3.12／3.13、uv，以及相容 AVX2/FMA 的 x86_64 CPU。前端是原生 JavaScript，不需 Node 建置。

```bash
bash scripts/setup.sh
.venv/bin/python -m pytest -q
bash scripts/start.sh

# 隔離資料，真實 KataGo、同時正式對局與 50 個模擬客戶端
.venv/bin/python scripts/smoke.py --teams 32 --board 19 --visits 128
.venv/bin/python scripts/lan_smoke.py
node scripts/browser-smoke.cjs

# 首次預載及重啟保留資料
.venv/bin/python scripts/roster_smoke.py
```

`uv.lock` 鎖定依賴。KataGo v1.16.3 Eigen AVX2 官方套件經 SHA-256 驗證，預設 b6c96 小模型、128 visits 僅供開發與預演。保持 `reportAnalysisWinratesAs = SIDETOMOVE`。正式模型、GPU、visits、場地區網及 50 台實際電腦仍須驗收；模擬客戶端不能取代現場測試。

```bash
uv sync --locked --group build
.venv/bin/python scripts/build_portable.py --output dist/yanghai-linux-x64-new
```

Windows exe 必須在 Windows x64 建置。套件包含主辦指定的固定隊伍碼表，管理金鑰仍在每台主機本機產生。完整資料庫備份含比賽資料與操作碼；JSON 匯出移除操作碼。不要將管理金鑰提交至 Git。雲端環境設定草稿與正式主機部署是不同流程。
