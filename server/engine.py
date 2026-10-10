"""Persistent KataGo JSON analysis subprocess; no simulated tournament AI."""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path


class KataGo:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.process = None
        self.pending = {}
        self.reader = None
        self.failure = "尚未啟動"
        self.limit = asyncio.Semaphore(int(os.getenv("AI_CONCURRENCY", "1")))

    async def start(self):
        binary = os.getenv("KATAGO_BIN", str(self.runtime / "katago" / "squashfs-root" / "AppRun"))
        model = os.getenv("KATAGO_MODEL", str(self.runtime / "model.txt.gz"))
        config = os.getenv("KATAGO_CONFIG", str(self.runtime / "analysis.cfg"))
        if not all(Path(p).is_file() for p in (binary, model, config)):
            self.failure = "找不到 KataGo 執行檔、模型或設定；請執行 scripts/setup.sh"
            return
        viewpoint = None
        for line in Path(config).read_text().splitlines():
            entry = line.split("#", 1)[0].strip()
            if "=" in entry:
                key, value = (part.strip() for part in entry.split("=", 1))
                if key == "reportAnalysisWinratesAs":
                    viewpoint = value
        if viewpoint != "SIDETOMOVE":
            self.failure = "KataGo 必須明確設定 reportAnalysisWinratesAs = SIDETOMOVE，以免勝率觀點反轉"
            return
        log = open(self.runtime / "katago.log", "ab")
        environment = os.environ.copy()
        # Frozen Python changes its library search path. An external KataGo must
        # use its own libraries, not whichever copies PyInstaller bundled.
        if getattr(sys, "frozen", False):
            original = environment.get("LD_LIBRARY_PATH_ORIG")
            if original is None:
                environment.pop("LD_LIBRARY_PATH", None)
            else:
                environment["LD_LIBRARY_PATH"] = original
        try:
            self.process = await asyncio.create_subprocess_exec(
                binary, "analysis", "-model", model, "-config", config,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
                limit=8 * 1024 * 1024, env=environment)
            self.reader = asyncio.create_task(self._read())
            # A real startup query verifies model load, IPC and successful analysis.
            await self.analyze([], {"board_size": 9, "komi": 7.5, "rules": "chinese", "visits": 16})
            self.failure = ""
        except Exception as exc:
            self.failure = f"KataGo 啟動失敗：{type(exc).__name__}（詳見 .runtime/katago.log）"
            await self.close()
        finally:
            log.close()

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                data = json.loads(line)
                future = self.pending.get(data.get("id"))
                if future and not future.done() and not data.get("isDuringSearch", False):
                    if data.get("warning"):
                        future.set_exception(RuntimeError("KataGo 設定警告：" + str(data["warning"])))
                    elif data.get("error"):
                        future.set_exception(RuntimeError(data["error"]))
                    elif data.get("moveInfos"):
                        future.set_result(data)
            self.failure = "KataGo 程序已停止"
        except Exception as exc:
            self.failure = f"KataGo 回應讀取失敗：{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            for f in self.pending.values():
                if not f.done():
                    f.set_exception(RuntimeError(self.failure))

    async def analyze(self, moves, settings):
        async with self.limit:
            if not self.process or self.process.returncode is not None:
                raise RuntimeError(self.failure)
            ident = uuid.uuid4().hex
            future = asyncio.get_running_loop().create_future()
            self.pending[ident] = future
            rules = "chinese-ogs" if settings["rules"] == "chinese" else settings["rules"]
            query = {"id": ident, "moves": moves, "initialPlayer": "B", "rules": rules,
                     "komi": settings["komi"], "boardXSize": settings["board_size"],
                     "boardYSize": settings["board_size"], "maxVisits": settings["visits"]}
            try:
                self.process.stdin.write((json.dumps(query) + "\n").encode())
                await self.process.stdin.drain()
                return await asyncio.wait_for(future, timeout=120)
            finally:
                self.pending.pop(ident, None)

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
