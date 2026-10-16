"""Real AI + 50 clients over this machine's LAN interface, using isolated data."""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.smoke import run as tournament_smoke


def main(executable=None, port=8768):
    with tempfile.TemporaryDirectory(prefix="yanghai-lan-") as directory:
        temp = Path(directory)
        data = temp/"data"
        if executable:
            executable = Path(executable).resolve()
            bundle = executable.parent
            settings = json.loads((bundle/"tournament.example.json").read_text())
            for field in ("katago_bin", "katago_model", "katago_config"):
                settings[field] = str(bundle/settings[field])
            command = [str(executable)]
        else:
            from launcher import defaults
            settings = defaults()
            for field in ("katago_bin", "katago_model", "katago_config"):
                settings[field] = str(ROOT/settings[field])
            command = [sys.executable, str(ROOT/"launcher.py")]
        # Deliberately start from an old loopback setting: --lan must override it.
        settings.update({"host": "127.0.0.1", "port": port, "data_dir": str(data), "open_browser": False})
        config = temp/"tournament.json"
        original = json.dumps(settings)
        config.write_text(original)
        command += ["--config", str(config), "--lan", "--empty-roster", "--no-browser", "--no-pause"]
        environment = os.environ.copy()
        for name in ("KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "AI_CONCURRENCY", "TOURNAMENT_DATA"):
            environment.pop(name, None)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            environment[name] = "http://127.0.0.1:1"
        with (temp/"server.log").open("w+") as log:
            process = subprocess.Popen(command, cwd=temp, env=environment, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=3) as client:
                    deadline = time.monotonic()+45
                    while time.monotonic()<deadline:
                        if process.poll() is not None:
                            raise AssertionError("LAN server exited before readiness")
                        try:
                            health = client.get("/api/health")
                            if health.status_code == 200:
                                assert health.json()["ai_ready"], "real KataGo not ready"
                                break
                        except httpx.ConnectError:
                            pass
                        time.sleep(.25)
                    else:
                        raise AssertionError("LAN server did not become ready")
                    assert client.get("/api/admin/network").status_code == 401
                    token = (data/"admin-token").read_text().strip()
                    network = client.get("/api/admin/network", headers={"Authorization": "Bearer "+token}).json()
                    assert network["lan_enabled"] and network["bind_host"] == "0.0.0.0"
                    assert network["port"] == port and network["interfaces"], "no local LAN interface to test"
                    assert token not in json.dumps(network["interfaces"]), "share URL exposed administrator token"
                address = network["interfaces"][0]["address"]
                with httpx.Client(base_url=f"http://{address}:{port}", trust_env=False, timeout=5) as participant:
                    for path in ("/", "/static/app.js", "/static/style.css", "/api/health"):
                        response = participant.get(path)
                        assert response.status_code == 200 and len(response.content)>20
                report = asyncio.run(tournament_smoke(port, data, 32, 19, 128, host=address))
                assert config.read_text() == original, "LAN startup rewrote settings"
                report.update({"lan_interface_http_verified": True, "loopback_config_overridden": True,
                               "network_details_require_admin": True, "public_urls_contain_no_tokens": True,
                               "packaged_app": bool(executable), "platform": sys.platform})
                (ROOT/".runtime").mkdir(exist_ok=True)
                (ROOT/".runtime/lan-smoke-report.json").write_text(json.dumps(report, indent=2)+"\n")
                print(json.dumps(report, indent=2))
            except Exception:
                log.flush(); log.seek(0)
                print(log.read()[-3500:])
                raise
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        # This isolated test tree only; leave no KataGo child behind.
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    else:
                        process.terminate()
                    process.wait(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    main(args.executable, args.port)
