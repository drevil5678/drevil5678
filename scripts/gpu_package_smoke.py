"""Verify the GPU bundle, honest fallback and real 12-table CPU baseline on CI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_portable import GPU_SHA256, GPU_URL
from server.gpu import nvidia_device, opencl_devices


def main(executable):
    executable = Path(executable).resolve()
    bundle = executable.parent
    provenance = json.loads((bundle / "BUILD-INFO.json").read_text(encoding="utf-8"))
    assert provenance["gpu_binary_url"] == GPU_URL
    assert provenance["gpu_binary_archive_sha256"] == GPU_SHA256
    for filename in ("katago-gpu/katago.exe", "config/analysis-gpu.cfg", "Check-GPU.cmd",
                     "Start-LAN-GPU.cmd", "Start-LAN-CPU.cmd", "Benchmark-12-Tables.cmd", "GPU-AND-BENCHMARK.md"):
        assert (bundle / filename).is_file(), "missing GPU package component: " + filename
    settings = json.loads((bundle / "tournament.example.json").read_text(encoding="utf-8-sig"))
    assert settings["engine_mode"] == "auto"
    for field in ("katago_bin", "katago_model", "katago_config", "katago_gpu_bin", "katago_gpu_config"):
        settings[field] = str(bundle / settings[field])
    environment = os.environ.copy()
    for key in ("KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "KATAGO_ENGINE_MODE", "KATAGO_GPU_BIN",
                "KATAGO_GPU_CONFIG", "AI_CONCURRENCY", "AI_GPU_CONCURRENCY", "KATAGO_GPU_DEVICE"):
        environment.pop(key, None)
    try:
        detected = nvidia_device(opencl_devices()) is not None
    except RuntimeError:
        detected = False
    with tempfile.TemporaryDirectory(prefix="yanghai-gpu-package-") as directory:
        temporary = Path(directory)
        data = temporary / "data"
        data.mkdir()
        # Actual event settings are read, but the database and admin key stay unchanged.
        database = data / "tournament.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("INSERT INTO state VALUES (1, ?)", (json.dumps({"settings": {
                "board_size": 19, "rules": "chinese", "komi": 7.5, "visits": 128},
                "teams": [], "games": [], "rounds": [], "marker": "preserve-test-event"}),))
        db.close()
        admin = data / "admin-token"
        admin.write_text("isolated-test-admin-key", encoding="utf-8")
        original_db, original_admin = database.read_bytes(), admin.read_bytes()
        settings.update(data_dir=str(data), open_browser=False)
        config = temporary / "tournament.json"
        original_config = json.dumps(settings)
        config.write_text(original_config, encoding="utf-8")

        def invoke(arguments, success=True):
            result = subprocess.run([str(executable), "--config", str(config), "--no-pause", *arguments],
                                    cwd=temporary, env=environment, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=750)
            if success:
                assert result.returncode == 0, (result.stdout[-3000:], result.stderr[-3000:])
                ready = [line.removeprefix("AI_READY ") for line in result.stdout.splitlines() if line.startswith("AI_READY ")]
                assert ready, "no confirmed real-engine readiness report"
                return json.loads(ready[-1])
            assert result.returncode != 0, "strict GPU succeeded without a usable NVIDIA GPU"
            assert "GPU 未啟動" in result.stdout + result.stderr
            assert "AI_READY " not in result.stdout
            return None

        automatic = invoke(["--engine", "auto", "--check"])
        if detected:
            assert automatic["backend"] == "opencl" and automatic["device"] is not None
            strict = invoke(["--engine", "gpu", "--check"])
            assert strict["backend"] == "opencl" and "NVIDIA GPU" in strict["label"]
        else:
            assert automatic["backend"] == "cpu" and automatic["device"] is None
            assert automatic["warning"] and automatic["label"] == "CPU"
            invoke(["--engine", "gpu", "--check"], success=False)
        cpu = invoke(["--engine", "cpu", "--benchmark", "--tables", "12", "--benchmark-rounds", "3"])
        assert cpu["backend"] == "cpu" and cpu["concurrency"] == 1
        benchmark = json.loads((data / "benchmark-report.json").read_text(encoding="utf-8"))
        assert benchmark["real_ai"] and benchmark["tables"] == 12 and benchmark["analyses"] == 36
        assert benchmark["settings"] == {"board_size": 19, "komi": 7.5, "rules": "chinese", "visits": 128}
        assert benchmark["latency_includes_queue"] and benchmark["p95_seconds"] > 0
        assert len({sample["position_sha256"] for sample in benchmark["samples"]}) == 36
        assert database.read_bytes() == original_db and admin.read_bytes() == original_admin
        assert config.read_text(encoding="utf-8") == original_config
        report = {"passed": True, "gpu_bundle_verified": True, "gpu_archive_sha256": GPU_SHA256,
                  "nvidia_detected": detected, "real_gpu_hardware_tested": detected,
                  "strict_gpu_cannot_silently_fallback": True, "actual_backend_reported": automatic["backend"],
                  "real_cpu_midgame_benchmark": benchmark, "formal_event_and_admin_key_preserved": True,
                  "platform": sys.platform}
        (ROOT / ".runtime").mkdir(exist_ok=True)
        (ROOT / ".runtime/gpu-package-smoke-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "real_cpu_midgame_benchmark"}, indent=2))
        print(json.dumps({key: benchmark[key] for key in ("tables", "analyses", "median_seconds", "p95_seconds", "max_seconds")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    main(parser.parse_args().executable)
