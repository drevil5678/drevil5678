# 從 GitHub 下載 Windows v0.8.1

若要四隊直接打四強的單機版，請選 **yanghai-windows-final-four-port8001**。完整解壓後雙擊 **Start-FinalFour-8001.cmd**，網址為 `http://127.0.0.1:8001`。四強一戰決勝，冠軍戰三戰兩勝，詳見[四強賽單機版](四強賽單機版.md)。

若要一台電腦給兩隊比賽的離線備援，請在同一個成功紀錄的 Artifacts 選 **yanghai-windows-duel-v0.8.1**。完整解壓後雙擊 **YanghaiDuel.exe**，不需要啟動 LAN 版。詳見[單機雙隊備用版](單機雙隊備用版.md)。

1. 登入 GitHub，開啟 [Build Windows test package](https://github.com/drevil5678/drevil5678/actions/workflows/windows-build.yml)。
2. 選擇最新一次成功的執行紀錄，在 **Artifacts** 點 `yanghai-windows-lan-v0.8.1`。
3. 先解壓附件 ZIP，再完整解壓裡面的程式 ZIP；保留所有資料夾，雙擊 **Start-LAN.cmd**。
4. 以本機 `data/admin-token` 登入管理頁，複製參賽網址。18 隊已預載並完成檢錄，規則確認後即可建立首輪；雙方骰子猜先並準備後開局。

主機不需另裝 Python 或 uv，選手只需瀏覽器。賽場、積分榜、隊伍紀錄、淘汰賽及單機練習共用同一網址。更新時保留原本 `data/` 和 `tournament.json`。

附件保留 14 天；過期需重新建置。Windows runner 會先執行測試、建置及真實 KataGo 檢查，成功才上傳；這不能取代現場硬體與區網彩排。CPU 版需要相容 AVX2 的 Intel／AMD x64 電腦。
