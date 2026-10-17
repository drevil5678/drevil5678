"""Verify the eight-team executable: real AI, restart, BO3 and isolated data."""
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
from scripts.integrated_practice_smoke import stop, unused_port


def main(executable=None, port=None):
    from launcher import defaults
    port = port or unused_port()
    with tempfile.TemporaryDirectory(prefix="yanghai-knockout-") as directory:
        temp = Path(directory)
        originals = {}
        for name in ("data", "duel-data"):
            folder = temp / name
            folder.mkdir()
            marker = folder / "tournament.sqlite3"
            marker.write_bytes((name + " must remain untouched").encode())
            originals[marker] = marker.read_bytes()
        settings = defaults()
        if executable:
            executable = Path(executable).resolve()
            bundle = executable.parent
            settings.update(katago_bin="katago/katago.exe", katago_model="model/model.txt.gz", katago_config="config/analysis.cfg")
            command = [str(executable)]  # The executable's name selects the mode.
        else:
            bundle = ROOT
            command = [sys.executable, str(ROOT / "launcher.py"), "--knockout"]
        for key in ("katago_bin", "katago_model", "katago_config", "katago_gpu_bin", "katago_gpu_config"):
            settings[key] = str(bundle / settings[key])
        settings.update(data_dir=str(temp / "data"), open_browser=False, host="0.0.0.0")
        config = temp / "knockout.json"
        original_config = json.dumps(settings)
        config.write_text(original_config, encoding="utf-8")
        command += ["--config", str(config), "--port", str(port), "--no-browser", "--no-pause"]
        environment = os.environ.copy()
        for key in ("KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "KATAGO_ENGINE_MODE", "TOURNAMENT_DATA",
                    "AI_CONCURRENCY", "KATAGO_GPU_BIN", "KATAGO_GPU_CONFIG", "AI_GPU_CONCURRENCY", "KATAGO_GPU_DEVICE"):
            environment.pop(key, None)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            environment[key] = "http://127.0.0.1:1"
        with (temp / "server.log").open("w+", encoding="utf-8") as log:
            for launch in range(2):
                process = subprocess.Popen(command, cwd=temp, env=environment, stdout=log, stderr=log)
                try:
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30) as client:
                        for _ in range(300):
                            assert process.poll() is None, "knockout executable exited"
                            try:
                                health = client.get("/api/health")
                                if health.status_code == 200:
                                    assert health.json()["mode"] == "knockout" and health.json()["ai_ready"]
                                    break
                            except httpx.ConnectError:
                                pass
                            time.sleep(.2)
                        else:
                            raise AssertionError("knockout executable did not start")
                        assert client.get("/").status_code == client.get("/static/knockout.js").status_code == 200
                        assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 403

                        def post(path, body, token=None):
                            response = client.post(path, json=body, headers={"Authorization": "Bearer " + token} if token else {})
                            assert response.status_code == 200, (path, response.status_code, response.text[:160])
                            return response.json()

                        def state():
                            return client.get("/api/state").json()

                        def match(ident):
                            return next(match for stage in client.get("/api/bracket").json()["stages"]
                                        for match in stage["matches"] if match["id"] == ident)

                        def wait_game(ident):
                            for _ in range(600):
                                game = client.get(f"/api/games/{ident}").json()
                                if game["status"] in ("playing", "finished"):
                                    return game
                                time.sleep(.05)
                            raise AssertionError("real AI analysis did not finish")

                        def host_win(ident, winner=None, **options):
                            game = post(f"/api/knockout/matches/{ident}/next", options)
                            winner = winner or game["seats"]["B"]["team"]
                            post(f"/api/duel/games/{game['id']}/action", {"action": "adjudicate", "winner": winner})
                            return game, winner

                        if launch == 0:
                            assert state()["teams"] == []
                            names = [f"自填隊伍{index}" for index in range(1, 9)]
                            post("/api/knockout/setup", {"names": names, "board_size": 9, "komi": 7.5, "visits": 32, "rules_confirmed": True})
                            assert [team["name"] for team in state()["teams"]] == names
                            game = post("/api/knockout/matches/quarter-1/next", {})
                            gid = game["id"]
                            credentials = state()["duel"]["credentials"][gid]
                            for _ in range(50):
                                game = client.get(f"/api/games/{gid}").json()
                                p = game["pregame"]
                                for team, token in credentials.items():
                                    if team not in p["rolls"]:
                                        post(f"/api/games/{gid}/dice", {"attempt": p["attempt"]}, token)
                                game = client.get(f"/api/games/{gid}").json()
                                if game["pregame"]["winner"]:
                                    break
                            else:
                                raise AssertionError("all dice tied")
                            p = game["pregame"]
                            post(f"/api/games/{gid}/color", {"attempt": p["attempt"], "color": "W"}, credentials[p["winner"]])
                            for token in credentials.values():
                                post(f"/api/games/{gid}/ready", {"attempt": p["attempt"], "ready": True}, token)
                            game = wait_game(gid)
                            for _ in range(2):
                                token = credentials[game["seats"][game["turn"]]["team"]]
                                post(f"/api/games/{gid}/move", {"version": game["version"], "move": game["candidates"][1]["move"]}, token)
                                game = wait_game(gid)
                            saved_board = game["board"]
                            post(f"/api/duel/games/{gid}/action", {"action": "pause", "reason": "八強備用版重啟檢查"})
                        else:
                            game = state()["games"][0]
                            assert game["id"] == gid and game["board"] == saved_board and game["status"] == "paused"
                            assert [team["name"] for team in state()["teams"]] == names
                            post(f"/api/duel/games/{gid}/action", {"action": "resume"})
                            game = wait_game(gid)
                            for _ in range(2):
                                token = credentials[game["seats"][game["turn"]]["team"]]
                                game = post(f"/api/games/{gid}/move", {"version": game["version"], "move": "pass"}, token)
                                if game["status"] != "finished":
                                    game = wait_game(gid)
                            assert game["result"]["reason"] == "score" and match("quarter-1")["winner"]
                            assert "KM[7.5]" in client.get(f"/api/games/{gid}/sgf").text
                            for index in (2, 3, 4):
                                host_win(f"quarter-{index}")
                            _, a = host_win("semi-1")
                            assert match("semi-1")["winner"]["id"] == a
                            assert match("semi-1")["best_of"] == 1
                            assert client.post("/api/knockout/matches/semi-1/next", json={}).status_code == 409
                            _, c = host_win("semi-2")
                            assert match("semi-2")["winner"]["id"] == c
                            assert client.post("/api/knockout/matches/semi-2/next", json={}).status_code == 409
                            final, _ = host_win("final-1", c, device_a=True)
                            assert final["remaining"] == {"B": 600, "W": 900}
                            assert client.get("/api/bracket").json()["champion"] is None
                            host_win("final-1", c)
                            assert client.get("/api/bracket").json()["champion"]["id"] == c
                            assert client.post("/api/knockout/matches/final-1/next", json={}).status_code == 409
                            exported = client.get("/api/duel/export").json()
                            assert len(exported["games"]) == 8 and exported["awards"] == []
                            assert all("token" not in seat for game in exported["games"] for seat in game["seats"].values())
                        assert all(marker.read_bytes() == original for marker, original in originals.items())
                        assert config.read_text(encoding="utf-8") == original_config
                except Exception:
                    log.flush(); log.seek(0); print(log.read()[-3500:])
                    raise
                finally:
                    stop(process)
        report = {"passed": True, "packaged_app": bool(executable), "platform": sys.platform,
                  "real_ai_games": 1, "other_games": "organizer adjudication to verify advancement",
                  "manual_eight_names": True, "quarter_and_semifinal_bo1": True, "final_bo3": True,
                  "final_two_zero": True, "restart_preserves_board_and_names": True,
                  "champion_recorded": True, "sgf_and_json": True, "formal_and_duel_data_untouched": True}
        (ROOT / ".runtime").mkdir(exist_ok=True)
        (ROOT / ".runtime/knockout-smoke-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    main(args.executable, args.port)
