"""Check offline two-person practice with real AI and untouched formal data."""
import argparse
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


def main(executable=None, port=8770):
    with tempfile.TemporaryDirectory(prefix="yanghai-practice-") as directory:
        temp = Path(directory)
        formal = temp/"data"
        formal.mkdir()
        marker = formal/"tournament.sqlite3"
        marker.write_bytes(b"This formal database must not be opened or modified by practice.")
        before = marker.read_bytes()
        if executable:
            executable = Path(executable).resolve()
            bundle = executable.parent
            settings = json.loads((bundle/"tournament.example.json").read_text())
            command = [str(executable)]
        else:
            from launcher import defaults
            bundle = ROOT
            settings = defaults()
            command = [sys.executable, str(ROOT/"launcher.py")]
        for field in ("katago_bin", "katago_model", "katago_config", "katago_gpu_bin", "katago_gpu_config"):
            settings[field] = str(bundle/settings[field])
        settings.update({"host": "0.0.0.0", "port": port, "data_dir": str(formal), "open_browser": False})
        config = temp/"tournament.json"
        original = json.dumps(settings)
        config.write_text(original)
        command += ["--config", str(config), "--practice", "--port", str(port), "--no-browser", "--no-pause"]
        environment = os.environ.copy()
        for key in ("KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "AI_CONCURRENCY", "TOURNAMENT_DATA", "KATAGO_ENGINE_MODE", "KATAGO_GPU_BIN", "KATAGO_GPU_CONFIG", "AI_GPU_CONCURRENCY", "KATAGO_GPU_DEVICE"):
            environment.pop(key, None)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            environment[key] = "http://127.0.0.1:1"
        with (temp/"server.log").open("w+") as log:
            process = subprocess.Popen(command, cwd=temp, env=environment, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=180) as client:
                    for _ in range(180):
                        if process.poll() is not None:
                            raise AssertionError("practice process exited early")
                        try:
                            health = client.get("/api/health")
                            if health.status_code == 200:
                                assert health.json()["mode"] == "practice" and health.json()["ai_ready"]
                                break
                        except httpx.ConnectError:
                            pass
                        time.sleep(.25)
                    else:
                        raise AssertionError("practice never became ready")
                    assert client.get("/").status_code == 200
                    for asset in ("/static/practice.js", "/static/style.css"):
                        response = client.get(asset)
                        assert response.status_code == 200 and len(response.content) > 100
                    assert client.get("/api/admin/state").status_code == 404
                    assert client.get("/api/state").status_code == 404
                    def post(path, body):
                        r = client.post(path, json=body)
                        assert r.status_code == 200, (path, r.status_code)
                        return r.json()["game"]
                    def version(game):
                        return {"game_id": game["id"], "version": game["version"]}
                    game = post("/api/practice/new", {"board_size": 19, "komi": 7.5})
                    assert game["settings"]["komi"] == 7.5 and game["status"] == "playing"
                    forbidden = client.post("/api/practice/move", json={**version(game), "move": game["forbidden"]})
                    assert forbidden.status_code == 400
                    for color in ("B", "W"):
                        assert game["turn"] == color
                        game = post("/api/practice/move", {**version(game), "move": game["candidates"][1]["move"]})
                    assert len(game["moves"]) == 2
                    assert "KM[7.5]" in client.get("/api/practice/sgf").text
                    game = post("/api/practice/undo", version(game))
                    assert len(game["moves"]) == 1 and game["turn"] == "W"
                    game = post("/api/practice/finish", version(game))
                    assert game["status"] == "finished" and "result" not in game
                from server.lan import network_info
                for interface in network_info("0.0.0.0", port)["interfaces"]:
                    try:
                        with httpx.Client(trust_env=False, timeout=1) as remote:
                            response = remote.get(f"http://{interface['address']}:{port}/api/health")
                            assert response.status_code == 403, "practice unexpectedly reachable on LAN"
                    except (httpx.ConnectError, httpx.TimeoutException):
                        pass
                assert marker.read_bytes() == before
                assert not (formal/"admin-token").exists() and not (formal/"backups").exists()
                assert not list((temp/"practice-data").glob("*.sqlite3"))
                assert config.read_text() == original
                report = {"passed": True, "mode": "offline two-person", "real_ai": True, "komi": 7.5,
                          "alternating_moves": True, "forbidden_move_rejected": True, "undo_and_sgf": True,
                          "formal_data_untouched": True, "lan_access_blocked": True,
                          "packaged_app": bool(executable), "platform": sys.platform}
                (ROOT/".runtime").mkdir(exist_ok=True)
                (ROOT/".runtime/practice-smoke-report.json").write_text(json.dumps(report, indent=2)+"\n")
                print(json.dumps(report, indent=2))
            except Exception:
                log.flush(); log.seek(0); print(log.read()[-3500:])
                raise
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                    else:
                        process.terminate()
                    process.wait(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    main(args.executable, args.port)
