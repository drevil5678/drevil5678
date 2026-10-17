# NVIDIA GPU 與 12 桌測速（v0.8.1）

適用 Windows 10／11 x64 與 NVIDIA 獨立顯卡，包含 RTX 4050 Laptop。套件已附經官方 SHA-256 核對的 KataGo OpenCL 引擎，使用顯示卡驅動提供的 OpenCL；使用前請安裝正常運作的 NVIDIA 顯示卡驅動。此版本沒有在你的筆電上實測，不能先承諾每手延遲。

## 啟動與確認

1. 完整解壓套件，筆電插電並保持散熱。升級時保留原本 `data/` 與 `tournament.json`。
2. 雙擊 **Check-GPU.cmd**。第一次會調校 GPU，可能需要數分鐘；成功必須顯示 NVIDIA GPU 與實際顯卡名稱。失敗會明確顯示原因，不會用 CPU 代替成功結果。
3. 一般使用 **Start-LAN.cmd**：自動選 NVIDIA GPU；若無法啟用，會改用 CPU 並顯示原因。要確保比賽一定使用顯卡，請用 **Start-LAN-GPU.cmd**；此入口在 GPU 失敗時維持 AI 未就緒，不能開正式比賽。需要 CPU 時使用 **Start-LAN-CPU.cmd**。
4. 登入主辦管理，查看區網開賽區的 AI 標示。成功時應顯示 `NVIDIA GPU · NVIDIA GeForce RTX 4050 Laptop GPU`，以及同時分析數、分析中與排隊數；不能只根據一般「AI 已就緒」推斷顯卡已使用。

會依 OpenCL 的裝置編號選取 NVIDIA GPU，避免選到 Radeon 740M 內顯。GPU 預設同時分析 4 局、每局 2 個搜尋執行緒；正式賽與練習共用同一個模型與分析佇列。CPU 保持原本預設一次一局。切換只發生在程式啟動時；對局中 GPU 中斷會暫停受影響棋局，交由主辦處理，不能自行改用另一個引擎繼續判定。

## 在你的電腦量測 12 桌

1. 先停止賽事伺服器，再雙擊 **Benchmark-12-Tables.cmd**。同一資料目錄有伺服器運作時會拒絕測速，避免影響正式賽。
2. 工具要求 NVIDIA GPU 成功啟動，接著做 3 輪、每輪同時送出 12 個不同的合法中盤局面，共 36 次真實分析。讀取現有賽事棋盤、規則、貼目與 visits；尚未有賽事時預設 19 路、中國規則、7.5 目、128 visits。
3. 看結尾的中位數、95% 分析等待及最慢時間，**全部包含排隊時間**。報告存於 `data/benchmark-report.json`，含實際引擎、顯卡、並行數、模型摘要與設定。測速不建立隊伍、不改動比賽紀錄，也不重新產生管理金鑰。

這是 AI 高峰負載測速；正式場地仍需測選手連線、練習與比賽同時運作，以及整輪時間。開發小模型的結果不能代替正式模型或不同 visits 的效能，更不能代表正式賽 AI 強度已驗收。調整模型或 visits 後要重測；不要只為讓測速變快而改動已確定的正式賽規則。

## 設定與舊版升級

新的空白設定檔會包含：

```json
{
  "engine_mode": "auto",
  "katago_gpu_bin": "katago-gpu/katago.exe",
  "katago_gpu_config": "config/analysis-gpu.cfg",
  "gpu_concurrency": 4,
  "gpu_device": -1
}
```

`gpu_device: -1` 自動選 NVIDIA 裝置；指定編號也必須對應 NVIDIA GPU。`gpu_concurrency` 支援 1–8，需以測速判斷是否適合；不是愈高愈快。無需手動安裝 Python、CUDA 或 cuDNN。

原本的 `tournament.json` 不會被覆寫。未提供 GPU 路徑時使用程式隨附檔案；明確指定的相對路徑仍相對於設定檔所在資料夾。使用隨附 CPU 路徑的舊設定可自動使用新 GPU 引擎；自訂引擎路徑或既有 KATAGO_BIN／KATAGO_CONFIG 環境設定會被保留，避免擅自換掉原引擎。需要明確選 GPU 可用上述 GPU 啟動器，或命令列 `--engine gpu`。KATAGO_MODEL 與賽事 visits 不因選 GPU 而改動。

CPU 版本仍需相容 AVX2/FMA 的處理器。GPU 編譯套件來源：[KataGo v1.16.3 官方發布](https://github.com/lightvector/KataGo/releases/tag/v1.16.3)；後端需求：[官方說明](https://github.com/lightvector/KataGo/blob/v1.16.3/README.md#opencl-vs-cuda-vs-tensorrt-vs-eigen)。
