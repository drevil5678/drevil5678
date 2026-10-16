"""Persistent KataGo JSON analysis subprocess; no simulated tournament AI."""
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from .gpu import gpu_config, nvidia_device, opencl_devices


class KataGo:
    def __init__(self, runtime):
        self.runtime = Path(runtime).resolve()
        self.process = None
        self.pending = {}
        self.reader = None
        self.failure = "尚未啟動"
        self.limit = asyncio.Semaphore(int(os.getenv("AI_CONCURRENCY", "1")))
        self.backend = "none"
        self.device = None
        self.warning = ""
        self.concurrency = int(os.getenv("AI_CONCURRENCY", "1"))
        self.active = 0
        self.waiting = 0
        self.mode = os.getenv("KATAGO_ENGINE_MODE", "custom")
        self._closing = False

    def status(self):
        label = ("NVIDIA GPU · " + self.device["name"]) if self.device and self.backend == "opencl" else {
            "cpu": "CPU", "custom": "自訂引擎", "opencl": "自訂 OpenCL 引擎",
            "cuda": "自訂 CUDA 引擎", "tensorrt": "自訂 TensorRT 引擎", "none": "尚未啟動"}.get(self.backend, self.backend)
        return {"ready": not self.failure, "mode": self.mode, "backend": self.backend, "label": label,
                "device": self.device, "concurrency": self.concurrency, "active": self.active,
                "waiting": self.waiting, "warning": self.warning, "error": self.failure}

    @staticmethod
    def external_environment():
        environment = os.environ.copy()
        if getattr(sys, "frozen", False):
            original = environment.get("LD_LIBRARY_PATH_ORIG")
            if original is None:
                environment.pop("LD_LIBRARY_PATH", None)
            else:
                environment["LD_LIBRARY_PATH"] = original
        return environment

    async def version_backend(self, binary):
        process = await asyncio.create_subprocess_exec(
            str(binary), "version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=self.external_environment())
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 15)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        text = output.decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise RuntimeError("KataGo 版本檢查失敗，請確認引擎及顯示卡驅動")
        for marker, backend in (("Using Eigen(CPU) backend", "cpu"), ("Using OpenCL backend", "opencl"),
                                ("Using CUDA backend", "cuda"), ("Using TensorRT backend", "tensorrt")):
            if marker in text:
                return backend
        return "custom"

    async def start(self):
        self.runtime.mkdir(parents=True, exist_ok=True)
        binary = Path(os.getenv("KATAGO_BIN", str(self.runtime / "katago" / "squashfs-root" / "AppRun"))).resolve()
        model = Path(os.getenv("KATAGO_MODEL", str(self.runtime / "model.txt.gz"))).resolve()
        config = Path(os.getenv("KATAGO_CONFIG", str(self.runtime / "analysis.cfg"))).resolve()
        if self.mode not in ("auto", "cpu", "gpu", "custom"):
            self.failure = "AI 引擎模式須為 auto、cpu、gpu 或 custom"
            return
        if self.mode in ("auto", "gpu"):
            try:
                gpu_binary = Path(os.getenv("KATAGO_GPU_BIN", "katago-gpu/katago.exe")).resolve()
                template = Path(os.getenv("KATAGO_GPU_CONFIG", "config/analysis-gpu.cfg")).resolve()
                if not gpu_binary.is_file() or not template.is_file():
                    raise RuntimeError("找不到隨附的 GPU 引擎或設定檔")
                devices = await asyncio.to_thread(opencl_devices)
                device = nvidia_device(devices, int(os.getenv("KATAGO_GPU_DEVICE", "-1")))
                if device is None:
                    raise RuntimeError("未找到可用的 NVIDIA OpenCL 顯示卡；請確認 NVIDIA 驅動")
                concurrency = int(os.getenv("AI_GPU_CONCURRENCY", "4"))
                if not 1 <= concurrency <= 8:
                    raise RuntimeError("GPU 同時分析數須為 1–8")
                generated = gpu_config(template, self.runtime / "analysis-gpu.generated.cfg", device, concurrency)
                print("正在啟用 NVIDIA GPU：" + device["name"] + "；首次調校可能需要數分鐘。", flush=True)
                await self._launch(gpu_binary, model, generated, "opencl", device, concurrency, startup_timeout=600)
                print("AI 已就緒：" + self.status()["label"], flush=True)
                return
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as exc:
                await self.close()
                reason = (str(exc) or ("GPU 初始化逾時" if isinstance(exc, TimeoutError) else type(exc).__name__))[:300]
                self.backend, self.device = "none", None
                if self.mode == "gpu":
                    self.failure = "GPU 未啟動：" + reason + "（詳見 katago.log）"
                    return
                self.warning = "GPU 未啟動，改用 CPU：" + reason
                print(self.warning, flush=True)
        try:
            await self._launch(binary, model, config, "cpu" if self.mode == "cpu" else None,
                               None, int(os.getenv("AI_CONCURRENCY", "1")), startup_timeout=120)
            print("AI 已就緒：" + self.status()["label"], flush=True)
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as exc:
            self.failure = "KataGo 啟動失敗：" + str(exc)[:300] + "（詳見 katago.log）"
            await self.close()

    async def _launch(self, binary, model, config, expected_backend, device, concurrency, startup_timeout):
        if not all(Path(p).is_file() for p in (binary, model, config)):
            raise RuntimeError("找不到 KataGo 執行檔、模型或設定")
        viewpoint = None
        for line in Path(config).read_text(encoding="utf-8-sig").splitlines():
            entry = line.split("#", 1)[0].strip()
            if "=" in entry:
                key, value = (part.strip() for part in entry.split("=", 1))
                if key == "reportAnalysisWinratesAs":
                    viewpoint = value
        if viewpoint != "SIDETOMOVE":
            raise RuntimeError("KataGo 必須明確設定 reportAnalysisWinratesAs = SIDETOMOVE，以免勝率觀點反轉")
        backend = await self.version_backend(binary)
        if expected_backend and backend != expected_backend:
            raise RuntimeError("KataGo 引擎種類與所選模式不符")
        self.backend, self.device, self.concurrency = backend, device, concurrency
        self.model = str(model)
        self.limit = asyncio.Semaphore(concurrency)
        self._closing = False
        log = open(self.runtime / "katago.log", "ab")
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(binary), "analysis", "-model", str(model), "-config", str(config),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log,
                limit=8 * 1024 * 1024, env=self.external_environment(), cwd=str(self.runtime.resolve()))
            self.reader = asyncio.create_task(self._read())
            # A real startup query verifies model load, IPC and successful analysis.
            await self._query([], {"board_size": 19 if device else 9, "komi": 7.5, "rules": "chinese", "visits": 16}, startup_timeout)
            self.failure = ""
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
            if not self._closing:
                self.failure = "KataGo 程序已停止"
        except Exception as exc:
            self.failure = f"KataGo 回應讀取失敗：{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            for f in self.pending.values():
                if not f.done():
                    f.set_exception(RuntimeError(self.failure))

    async def analyze(self, moves, settings):
        self.waiting += 1
        acquired = False
        try:
            async with self.limit:
                acquired = True
                self.waiting -= 1
                self.active += 1
                try:
                    return await self._query(moves, settings, 120)
                finally:
                    self.active -= 1
        finally:
            if not acquired:
                self.waiting -= 1

    async def _query(self, moves, settings, timeout):
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
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self.pending.pop(ident, None)

    async def close(self):
        self._closing = True
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        self.reader = None
        self.process = None
        if not self.failure:
            self.failure = "KataGo 已停止"
