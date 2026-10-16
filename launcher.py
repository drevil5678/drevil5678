"""Portable local server entry point. Tournament data stays outside the bundle."""
import argparse
import asyncio
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from pathlib import Path


class InstanceLock:
    """OS-held lock: crash-safe, with no stale PID files to delete manually."""
    def __init__(self, directory):
        self.path = Path(directory) / "server.lock"
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        if self.path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError("同一資料目錄已有伺服器運作，請使用現有視窗；不會覆寫棋局。") from None
        return self

    def __exit__(self, *_):
        if self.file:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


def base_directory():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def defaults():
    frozen = getattr(sys, "frozen", False)
    return {
        "host": "0.0.0.0", "port": 8000,
        "data_dir": "data" if frozen else ".runtime/local-data",
        "katago_bin": ("katago/katago.exe" if os.name == "nt" else "katago/AppRun") if frozen else ".runtime/katago/squashfs-root/AppRun",
        "katago_model": "model/model.txt.gz" if frozen else ".runtime/model.txt.gz",
        "katago_config": "config/analysis.cfg" if frozen else ".runtime/analysis.cfg",
        "ai_concurrency": 1, "auto_backup_seconds": 600, "open_browser": True,
    }


def load_settings(filename):
    filename = Path(filename).resolve()
    if not filename.exists():
        filename.parent.mkdir(parents=True, exist_ok=True)
        with filename.open("x", encoding="utf-8") as f:
            json.dump(defaults(), f, ensure_ascii=False, indent=2)
            f.write("\n")
    values = json.loads(filename.read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict):
        raise ValueError("tournament.json 必須是 JSON 物件")
    unknown = set(values) - set(defaults())
    if unknown:
        raise ValueError("未知的設定欄位：" + ", ".join(sorted(unknown)))
    settings = {**defaults(), **values}
    if not isinstance(settings["host"], str) or not settings["host"]:
        raise ValueError("host 必須是 IPv4 位址或主機名稱")
    if type(settings["port"]) is not int or not 1 <= settings["port"] <= 65535:
        raise ValueError("port 必須是 1–65535 的整數")
    if type(settings["ai_concurrency"]) is not int or not 1 <= settings["ai_concurrency"] <= 64:
        raise ValueError("ai_concurrency 必須是 1–64 的整數")
    if type(settings["auto_backup_seconds"]) is not int or settings["auto_backup_seconds"] < 60:
        raise ValueError("auto_backup_seconds 須至少 60 秒")
    if type(settings["open_browser"]) is not bool:
        raise ValueError("open_browser 必須是 true 或 false")
    for name in ("data_dir", "katago_bin", "katago_model", "katago_config"):
        if not isinstance(settings[name], str) or not settings[name].strip():
            raise ValueError(name + " 必須是非空白路徑")
        settings[name] = str((filename.parent / settings[name]).resolve())
    return settings


def backup_before_start(directory):
    """SQLite online-backup API includes committed WAL, unlike copying a .db."""
    directory = Path(directory)
    source = directory / "tournament.sqlite3"
    if not source.exists():
        return None
    backups = directory / "backups"
    backups.mkdir(exist_ok=True, mode=0o700)
    destination = backups / f"before-start-{time.time_ns()}.sqlite3"
    original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    target = None
    try:
        if original.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("資料庫完整性檢查未通過；保留原資料，請從已核對的備份恢復。")
        target = sqlite3.connect(destination)
        original.backup(target)
    except Exception:
        if target is not None:
            target.close()
            target = None
            destination.unlink(missing_ok=True)
        raise
    finally:
        if target is not None:
            target.close()
        original.close()
    destination.chmod(0o600)
    return destination


def browser_when_ready(port, stop, host="127.0.0.1", view="admin"):
    import urllib.request
    from server.lan import admin_url
    url = admin_url(host, port).split("/#", 1)[0]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(500):
        if stop.wait(.25):
            return
        try:
            with opener.open(url + "/api/health", timeout=1) as response:
                if json.load(response).get("ok"):
                    if not stop.is_set():
                        suffix = {"admin": "/#admin", "integrated-practice": "/#practice"}.get(view, "/")
                        webbrowser.open(url + suffix)
                    return
        except (OSError, ValueError):
            continue


def main(argv=None):
    parser = argparse.ArgumentParser(description="楊海分站賽 · 現場本地伺服器")
    parser.add_argument("--config", type=Path, default=base_directory()/"tournament.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--host", help="覆寫本次監聽位址，不修改設定檔")
    mode.add_argument("--lan", action="store_true", help="啟動區域網路網頁版，讓選手用瀏覽器加入")
    mode.add_argument("--practice", action="store_true", help="離線單機雙人練習，使用獨立資料與本機連線")
    mode.add_argument("--practice-lan", action="store_true", help="開啟主站同埠練習分頁，每個瀏覽器一桌，同機雙人或對電腦")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--empty-roster", action="store_true", help="新資料庫不預載名單，供獨立測試；不刪除現有隊伍")
    parser.add_argument("--check", action="store_true", help="真實 AI 啟動診斷，不建立／更動賽事資料庫")
    parser.add_argument("--no-pause", action="store_true", help="錯誤時不等待 Enter，供自動化使用")
    args = parser.parse_args(argv)
    practice_mode = args.practice
    listener, stop = None, threading.Event()
    try:
        settings = load_settings(args.config)
        host = "127.0.0.1" if args.practice else "0.0.0.0" if args.lan or args.practice_lan else args.host if args.host is not None else settings["host"]
        port = args.port if args.port is not None else 8001 if practice_mode else settings["port"]
        if not 1 <= port <= 65535:
            raise ValueError("port 必須是 1–65535")
        os.environ["TOURNAMENT_DATA"] = settings["data_dir"]
        for key, field in (("KATAGO_BIN", "katago_bin"), ("KATAGO_MODEL", "katago_model"), ("KATAGO_CONFIG", "katago_config"),
                           ("AI_CONCURRENCY", "ai_concurrency")):
            os.environ.setdefault(key, str(settings[field]))
        os.environ["AUTO_BACKUP_SECONDS"] = str(settings["auto_backup_seconds"])
        directory = Path(settings["data_dir"])
        if practice_mode:
            practice_directory = directory.parent/"practice-data"
            directory = practice_directory if practice_directory != directory else directory.parent/"practice-only"
            os.environ["TOURNAMENT_DATA"] = str(directory)
        with InstanceLock(directory):
            if args.check:
                from server.engine import KataGo
                async def check():
                    ai = KataGo(directory)
                    await ai.start()
                    try:
                        if ai.failure:
                            raise RuntimeError(ai.failure)
                        print("PASS: 真實 KataGo 模型載入與分析成功。")
                    finally:
                        await ai.close()
                asyncio.run(check())
                return 0
            # Reserve the port before opening the database; fail without altering
            # tournament records if another service owns it.
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((host, port))
                listener.listen(128)
            except OSError:
                raise RuntimeError(f"無法監聽 {host}:{port}，可能已有程式使用此連接埠；不會停止其他程式。") from None
            backup = None if practice_mode else backup_before_start(directory)
            if backup:
                print("已建立啟動前備份：", backup)
            print("雙人離線單機練習 · 不計正式賽積分" if practice_mode else "楊海分站賽本地伺服器 · 比賽與同機練習使用同一網址")
            print("賽事資料目錄：", directory)
            if not practice_mode:
                print("主辦方管理金鑰檔案：", directory / "admin-token", "（請在本機安全讀取）")
            print(f"本機練習連接埠：{port}，只允許此台電腦連線。" if args.practice else f"監聽位址：{host}，連接埠：{port}。其他電腦請輸入本主機的區域網路 IPv4 及此連接埠。")
            from server.lan import network_info
            binding = {"host": listener.getsockname()[0], "port": port}
            network = network_info(binding["host"], port)
            print("雙人練習頁：" if practice_mode else "主辦管理頁：", network["admin_url"].replace("/#admin", "/") if practice_mode else network["admin_url"])
            if not practice_mode:
                print("主站練習分頁：", network["admin_url"].replace("/#admin", "/#practice"), "（同機雙人或電腦，不可跨電腦入座）")
            for item in network["interfaces"]:
                url = item["url"].split("/#", 1)[0] + "/" if practice_mode else item["url"]
                print(f"{'練習' if practice_mode else '參賽'}網址（{item['name']}）：{url}")
            if network["warning"] and not args.practice:
                print(network["warning"])
            if not args.practice:
                print("選手電腦只需瀏覽器。Windows 防火牆請允許活動使用的私人網路；無法連線時檢查是否在同一區網。")
            print("練習棋局請先下載 SGF 保存；停止請按 Ctrl+C。" if practice_mode else "不要關閉本視窗；停止請按 Ctrl+C，服務會保存並暫停棋局。")
            import uvicorn
            if args.practice:
                from server.practice import create_practice_app
                application = create_practice_app(directory)
            else:
                from server.app import create_app
                application = create_app(directory, network=binding, bootstrap=not args.empty_roster)
                if application.state.initial_roster_loaded:
                    print("已建立內建 18 隊及預製固定點棋碼，全隊預設完成資格確認與檢錄。可直接列印 TEAM-CODES.html。")
            config = uvicorn.Config(application, host=host, port=port, workers=1, loop="asyncio", http="h11", ws="none", access_log=False)
            if not args.no_browser and settings["open_browser"]:
                view = "practice" if practice_mode else "integrated-practice" if args.practice_lan else "admin"
                threading.Thread(target=browser_when_ready, args=(port, stop, binding["host"], view), daemon=True).start()
            uvicorn.Server(config).run(sockets=[listener])
            return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print("啟動失敗：", str(exc), file=sys.stderr)
        if not args.no_pause and sys.stdin.isatty():
            try:
                input("按 Enter 關閉此視窗。")
            except (EOFError, KeyboardInterrupt):
                pass
        return 1
    finally:
        stop.set()
        if listener:
            listener.close()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
