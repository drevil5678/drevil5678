"""Exercise an offline two-team executable with real AI, restart and isolation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(executable=None, port=8775):
    from launcher import defaults
    with tempfile.TemporaryDirectory(prefix="yanghai-duel-") as directory:
        temp = Path(directory)
        formal = temp / "data"
        formal.mkdir()
        marker = formal / "tournament.sqlite3"
        original_db = b"formal event must remain untouched by the offline PK program"
        marker.write_bytes(original_db)
        settings = defaults()
        if executable:
            executable = Path(executable).resolve()
            bundle = executable.parent
            settings.update(katago_bin="katago/katago.exe", katago_model="model/model.txt.gz",
                            katago_config="config/analysis.cfg")
            command = [str(executable)]  # Its name must select duel mode itself.
        else:
            bundle = ROOT
            command = [sys.executable, str(ROOT / "launcher.py"), "--duel"]
        for field in ("katago_bin", "katago_model", "katago_config", "katago_gpu_bin", "katago_gpu_config"):
            settings[field] = str(bundle / settings[field])
        settings.update(data_dir=str(formal), host="0.0.0.0", open_browser=False)
        config = temp / "duel.json"
        original_config = json.dumps(settings)
        config.write_text(original_config, encoding="utf-8")
        command += ["--config", str(config), "--port", str(port), "--no-browser", "--no-pause"]
        environment = os.environ.copy()
        for key in ("KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "KATAGO_ENGINE_MODE", "TOURNAMENT_DATA",
                    "AI_CONCURRENCY", "KATAGO_GPU_BIN", "KATAGO_GPU_CONFIG", "AI_GPU_CONCURRENCY", "KATAGO_GPU_DEVICE"):
            environment.pop(key, None)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            environment[key] = "http://127.0.0.1:1"
        with (temp / "duel.log").open("w+", encoding="utf-8") as log:
            for launch in range(2):
                process = subprocess.Popen(command, cwd=temp, env=environment, stdout=log, stderr=log)
                try:
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30) as client:
                        deadline = time.monotonic() + 60
                        while time.monotonic() < deadline:
                            assert process.poll() is None, "offline executable exited early"
                            try:
                                health = client.get("/api/health")
                                if health.status_code == 200:
                                    assert health.json()["mode"] == "duel" and health.json()["ai_ready"]
                                    break
                            except httpx.ConnectError:
                                pass
                            time.sleep(.2)
                        else:
                            raise AssertionError("offline executable did not start")
                        assert client.get("/").status_code == 200
                        for asset in ("duel.js", "dice-stage.js", "app.js", "style.css"):
                            assert client.get("/static/" + asset).status_code == 200
                        assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 403
                        assert client.post("/api/duel/new", json={}, headers={"Origin": "https://evil.example"}).status_code == 403

                        def post(path, body, token=None):
                            result = client.post(path, json=body, headers={"Authorization": "Bearer " + token} if token else {})
                            assert result.status_code == 200, (path, result.status_code, result.text[:200])
                            return result.json()

                        def wait_game(ident, expected="playing"):
                            deadline = time.monotonic() + 30
                            while time.monotonic() < deadline:
                                current = client.get(f"/api/games/{ident}").json()
                                if current["status"] == expected:
                                    return current
                                time.sleep(.05)
                            raise AssertionError(f"expected {expected}; got {current['status']}")

                        state = client.get("/api/state").json()
                        if launch == 0:
                            assert not state["teams"] and not state["games"]
                            game = post("/api/duel/new", {"team_a": "備用甲隊", "team_b": "備用乙隊", "board_size": 19,
                                        "komi": 7.5, "rules_confirmed": True, "visits": 32, "championship": True, "device_a": True})
                            gid = game["id"]
                            assert game["remaining"] == {"B": 600, "W": 900}
                            assert client.post("/api/duel/new", json={"rules_confirmed": True}).status_code == 409
                            credentials = client.get("/api/state").json()["duel"]["credentials"][gid]
                            for _ in range(50):
                                game = client.get(f"/api/games/{gid}").json()
                                attempt = game["pregame"]["attempt"]
                                for team, token in credentials.items():
                                    if team not in game["pregame"]["rolls"]:
                                        post(f"/api/games/{gid}/dice", {"attempt": attempt}, token)
                                game = client.get(f"/api/games/{gid}").json()
                                if game["pregame"]["winner"]:
                                    break
                            else:
                                raise AssertionError("no non-tied dice result")
                            p = game["pregame"]
                            post(f"/api/games/{gid}/color", {"attempt": p["attempt"], "color": "W"}, credentials[p["winner"]])
                            for token in credentials.values():
                                post(f"/api/games/{gid}/ready", {"attempt": p["attempt"], "ready": True}, token)
                            game = wait_game(gid)
                            banned = client.post(f"/api/games/{gid}/move", json={"move": game["forbidden"], "version": game["version"]},
                                headers={"Authorization": "Bearer " + credentials[game["seats"][game["turn"]]["team"]]})
                            assert banned.status_code == 400
                            for _ in range(2):
                                token = credentials[game["seats"][game["turn"]]["team"]]
                                post(f"/api/games/{gid}/move", {"move": game["candidates"][1]["move"], "version": game["version"]}, token)
                                game = wait_game(gid)
                            assert len(game["moves"]) == 2
                            saved_board = game["board"]
                            post(f"/api/duel/games/{gid}/action", {"action": "pause", "reason": "備用機重啟檢查"})
                        else:
                            game = state["games"][0]
                            assert game["id"] == gid and game["board"] == saved_board and game["status"] == "paused"
                            assert state["duel"]["credentials"][gid] == credentials
                            post(f"/api/duel/games/{gid}/action", {"action": "resume"})
                            game = wait_game(gid)
                            for _ in range(2):
                                token = credentials[game["seats"][game["turn"]]["team"]]
                                game = post(f"/api/games/{gid}/move", {"move": "pass", "version": game["version"]}, token)
                                if game["status"] != "finished":
                                    game = wait_game(gid)
                            assert game["status"] == "finished" and game["result"]["reason"] == "score"
                            assert game["result"]["points"] == 1
                            assert "KM[7.5]" in client.get(f"/api/games/{gid}/sgf").text
                            exported = client.get("/api/duel/export").json()
                            assert len(exported["games"][0]["moves"]) == 4
                            assert "token" not in exported["games"][0]["seats"]["B"]
                        assert marker.read_bytes() == original_db
                        assert not (formal / "admin-token").exists() and not (formal / "backups").exists()
                        assert config.read_text(encoding="utf-8") == original_config
                except Exception:
                    log.flush(); log.seek(0); print(log.read()[-3500:])
                    raise
                finally:
                    if process.poll() is None:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                        else:
                            process.terminate()
                        process.wait(timeout=20)
        report = {"passed": True, "real_ai": True, "internet_proxies_blocked": True, "two_launches": True,
                  "formal_data_untouched": True, "dice_and_clocks": True, "two_teams_alternate": True,
                  "forbidden_move_rejected": True, "double_pass_scored": True, "sgf_and_json_export": True,
                  "packaged_app": bool(executable), "platform": sys.platform}
        (ROOT / ".runtime").mkdir(exist_ok=True)
        (ROOT / ".runtime/duel-smoke-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--port", type=int, default=8775)
    args = parser.parse_args()
    main(args.executable, args.port)
