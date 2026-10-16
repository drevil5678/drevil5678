"""GPU selection tests use explicit driver/engine doubles, never fake production rates."""
import asyncio
import ctypes
from pathlib import Path

import pytest

from launcher import base_directory, defaults, engine_environment, load_settings
from server.engine import KataGo
from server.gpu import gpu_config, nvidia_device, opencl_devices

AMD = {"index": 0, "name": "Radeon 740M", "vendor": "AMD", "gpu": True, "memory_bytes": 2 * 1024**3}
NVIDIA = {"index": 2, "name": "NVIDIA GeForce RTX 4050 Laptop GPU", "vendor": "NVIDIA Corporation", "gpu": True, "memory_bytes": 6 * 1024**3}
ENGINE_ENV = ("KATAGO_ENGINE_MODE", "KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "KATAGO_GPU_BIN",
              "KATAGO_GPU_CONFIG", "AI_CONCURRENCY", "AI_GPU_CONCURRENCY", "KATAGO_GPU_DEVICE")


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    # engine_environment writes process variables directly; register all of them
    # with monkeypatch even when initially absent so teardown restores them.
    for key in ENGINE_ENV:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)


class DriverCall:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class FakeOpenCL:
    def __init__(self):
        self.clGetPlatformIDs = DriverCall(self.platforms)
        self.clGetDeviceIDs = DriverCall(self.devices)
        self.clGetDeviceInfo = DriverCall(self.info)

    def platforms(self, count, output, total):
        if total is not None:
            ctypes.cast(total, ctypes.POINTER(ctypes.c_uint))[0] = 2
        if output is not None:
            output[0], output[1] = 10, 20
        return 0

    def devices(self, platform, mask, count, output, total):
        assert mask == 14  # Same mask as KataGo, not CL_DEVICE_TYPE_ALL.
        ids = [100, 101] if platform == 10 else [200]
        if total is not None:
            ctypes.cast(total, ctypes.POINTER(ctypes.c_uint))[0] = len(ids)
        if output is not None:
            for i, device in enumerate(ids):
                output[i] = device
        return 0

    def info(self, device, field, size, output, returned):
        data = {100: AMD, 101: {"name": "CPU", "vendor": "Intel", "gpu": False, "memory_bytes": 8 * 1024**3}, 200: NVIDIA}[device]
        if field in (0x102B, 0x102C):
            value = data["name" if field == 0x102B else "vendor"].encode() + b"\0"
            ctypes.memmove(output, value, len(value))
        else:
            ctypes.cast(output, ctypes.POINTER(ctypes.c_ulonglong))[0] = (4 if data["gpu"] else 2) if field == 0x1000 else data["memory_bytes"]
        return 0


def test_windows_driver_enumeration_keeps_katago_indices_and_selects_discrete_nvidia():
    devices = opencl_devices(FakeOpenCL())
    assert [device["index"] for device in devices] == [0, 1, 2]
    assert nvidia_device(devices) == NVIDIA
    assert nvidia_device(devices, 0) is None  # Never silently choose AMD's iGPU.
    assert nvidia_device(devices, 2) == NVIDIA
    assert nvidia_device([AMD]) is None


def test_gpu_config_replaces_all_higher_priority_device_aliases_and_preserves_winrate_view(tmp_path):
    source, output = tmp_path / "source.cfg", tmp_path / "generated.cfg"
    source.write_text("numAnalysisThreads = 1\nopenclDeviceToUseThread0 = 0\ndeviceToUse = 0\n"
                      "reportAnalysisWinratesAs = SIDETOMOVE\nnumSearchThreadsPerAnalysisThread = 2\n")
    gpu_config(source, output, NVIDIA, 4)
    text = output.read_text()
    assert "openclDeviceToUse = 2" in text and "numAnalysisThreads = 4" in text
    assert "openclDeviceToUseThread0" not in text and "\ndeviceToUse =" not in text
    assert "reportAnalysisWinratesAs = SIDETOMOVE" in text and "homeDataDir = katago-cache" in text


@pytest.fixture
def profile(tmp_path, monkeypatch):
    for key in ENGINE_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, filename in (("KATAGO_BIN", "cpu"), ("KATAGO_MODEL", "model"), ("KATAGO_CONFIG", "cpu.cfg"),
                          ("KATAGO_GPU_BIN", "gpu"), ("KATAGO_GPU_CONFIG", "gpu.cfg")):
        path = tmp_path / filename
        path.write_text("reportAnalysisWinratesAs = SIDETOMOVE\n")
        monkeypatch.setenv(key, str(path))
    monkeypatch.setattr("server.engine.opencl_devices", lambda: [AMD, NVIDIA])
    monkeypatch.setenv("KATAGO_ENGINE_MODE", "auto")
    return tmp_path


def install_launch_double(monkeypatch, fail_gpu=False):
    launches = []

    async def launch(self, binary, model, config, expected_backend, device, concurrency, startup_timeout):
        launches.append({"backend": expected_backend, "device": device, "concurrency": concurrency,
                         "config": Path(config).read_text(), "timeout": startup_timeout})
        if fail_gpu and device:
            raise RuntimeError("simulated GPU startup failure")
        self.backend = "opencl" if device else "cpu"
        self.device, self.concurrency, self.failure = device, concurrency, ""

    monkeypatch.setattr(KataGo, "_launch", launch)
    return launches


def test_auto_gpu_success_has_nvidia_identity_and_four_analysis_slots(profile, monkeypatch):
    launches = install_launch_double(monkeypatch)
    engine = KataGo(profile)
    asyncio.run(engine.start())
    assert not engine.failure and len(launches) == 1
    assert engine.status()["backend"] == "opencl" and engine.status()["device"] == NVIDIA
    assert engine.status()["concurrency"] == 4 and "RTX 4050" in engine.status()["label"]
    assert "openclDeviceToUse = 2" in launches[0]["config"]
    assert launches[0]["timeout"] == 600


@pytest.mark.parametrize("no_device", [True, False])
def test_auto_fallback_is_explicit_and_cpu_label_is_never_gpu(profile, monkeypatch, no_device):
    if no_device:
        monkeypatch.setattr("server.engine.opencl_devices", lambda: [AMD])
    launches = install_launch_double(monkeypatch, fail_gpu=True)
    engine = KataGo(profile)
    asyncio.run(engine.start())
    assert engine.status()["ready"] and engine.status()["backend"] == "cpu"
    assert engine.status()["device"] is None and engine.status()["label"] == "CPU"
    assert "改用 CPU" in engine.warning and launches[-1]["device"] is None


def test_strict_gpu_failure_never_launches_cpu(profile, monkeypatch):
    monkeypatch.setenv("KATAGO_ENGINE_MODE", "gpu")
    launches = install_launch_double(monkeypatch, fail_gpu=True)
    engine = KataGo(profile)
    asyncio.run(engine.start())
    assert len(launches) == 1 and launches[0]["device"] == NVIDIA
    assert engine.failure and not engine.status()["ready"] and engine.status()["device"] is None


def test_cpu_mode_never_probes_gpu(profile, monkeypatch):
    monkeypatch.setenv("KATAGO_ENGINE_MODE", "cpu")
    monkeypatch.setattr("server.engine.opencl_devices", lambda: pytest.fail("CPU mode probed GPU"))
    launches = install_launch_double(monkeypatch)
    engine = KataGo(profile)
    asyncio.run(engine.start())
    assert len(launches) == 1 and launches[0]["backend"] == "cpu"


def test_cancelled_waiter_does_not_leak_queue_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_CONCURRENCY", "1")

    async def exercise():
        engine = KataGo(tmp_path)
        release = asyncio.Event()

        async def query(*_):
            await release.wait()
            return {"moveInfos": []}

        monkeypatch.setattr(engine, "_query", query)
        tasks = [asyncio.create_task(engine.analyze([], {})) for _ in range(3)]
        await asyncio.sleep(.01)
        assert engine.active == 1 and engine.waiting == 2
        tasks[1].cancel()
        await asyncio.gather(tasks[1], return_exceptions=True)
        assert engine.active == 1 and engine.waiting == 1
        release.set()
        await asyncio.gather(tasks[0], tasks[2])
        assert engine.active == engine.waiting == 0

    asyncio.run(exercise())


def test_environment_keeps_custom_engine_and_does_not_edit_old_config(tmp_path, monkeypatch):
    for key in ENGINE_ENV:
        monkeypatch.delenv(key, raising=False)
    filename = tmp_path / "tournament.json"
    original = '{"engine_mode":"auto", "katago_bin":"custom/engine.exe"}'
    filename.write_text(original)
    settings = load_settings(filename)
    engine_environment(settings)
    import os
    assert os.environ["KATAGO_ENGINE_MODE"] == "custom"
    assert os.environ["KATAGO_BIN"] == str(tmp_path / "custom/engine.exe")
    assert filename.read_text() == original


def test_stock_auto_profile_can_upgrade_while_old_cpu_config_remains_unchanged(tmp_path, monkeypatch):
    for key in ENGINE_ENV:
        monkeypatch.delenv(key, raising=False)
    filename = tmp_path / "tournament.json"
    stock = defaults()
    import json
    original = json.dumps({"engine_mode": "auto", **{field: str(base_directory() / stock[field])
                         for field in ("katago_bin", "katago_config")}})
    filename.write_text(original)
    engine_environment(load_settings(filename))
    import os
    assert os.environ["KATAGO_ENGINE_MODE"] == "auto"
    assert filename.read_text() == original
