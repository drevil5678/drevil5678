# 從 GitHub 下載 Windows 測試版

更新日期：2026-10-11。程式已推送至 `codex/yanghai-local-test`；GitHub Windows runner 已成功完成測試、免安裝版建置及真實 KataGo 分析診斷，並上傳約 24.7 MB 的成品附件。

[開啟已成功的 Windows 成品下載頁](https://github.com/drevil5678/drevil5678/actions/runs/38068759175#artifacts)。請先登入 GitHub，再在 Artifacts 區域點選 `yanghai-windows-x64-v0.1.0`。

`.github/workflows/windows-build.yml` 會在 `codex/yanghai-local-test` 分支更新時使用 Windows runner 執行，安裝鎖定依賴、跑測試、建置本地程式，再用隨附的真實 KataGo 診斷。全部成功後才上傳可下載的 ZIP 與 SHA-256 摘要。

## 下載

1. 登入 GitHub，進入 `drevil5678/drevil5678` 儲存庫。
2. 選取 **Actions → Build Windows test package**，開啟最新一次成功的執行紀錄。
3. 在 **Artifacts** 區域點選 `yanghai-windows-x64-v0.1.0` 下載。若 Actions 被儲存庫設定停用，需要在 GitHub 啟用後重新執行。
4. GitHub 下載的附件 ZIP 內含另一個程式 ZIP 及 `.sha256`。先解壓附件，再完整解壓程式 ZIP，找到 `YanghaiTournament.exe`。
5. 保留執行檔旁的 `_internal`、`katago`、`model` 與 `config` 資料夾，雙擊執行檔啟動。測試主機不用另外安裝 Python 或 uv。

Artifacts 保留 14 天；到期後需重新建置。若執行紀錄失敗，不會提供宣稱成功的套件，需先檢查該次失敗的步驟。

## 第一次測試

程式自動開啟瀏覽器。以記事本安全讀取執行檔旁的 `data/admin-token`，在主辦管理頁登入。建立兩支虛構隊伍並完成檢錄，建立首輪，交付黑白方操作碼，再由主辦開賽。

保持程式視窗開啟；停止按 Ctrl+C。重新啟動會保留棋局並暫停，裁判核對後再恢復。詳細操作見 `本機測試快速指南.md` 與 `本地執行檔.md`。

Windows runner 診斷通過代表建置與 AI 載入通過，不等於你的現場電腦或 50 台區網已驗證。隨附 CPU 模型仍供開發與預演，需要相容 AVX2 的 Intel／AMD x64 電腦。
