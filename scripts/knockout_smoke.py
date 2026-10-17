"""Verify four-team startup, real AI, restart, final BO3 and isolated data."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.integrated_practice_smoke import stop
from server.lan import network_info
from server.initial_roster import INITIAL_CODES, INITIAL_ROSTER


def main(executable=None, port=None):
    from launcher import defaults
    explicit_port = port
    port = port or 8080
    interfaces = network_info("0.0.0.0", port)["interfaces"]
    assert interfaces, "a LAN IPv4 is required for the real network smoke test"
    address = interfaces[0]["address"]
    with tempfile.TemporaryDirectory(prefix="yanghai-final-four-") as directory:
        temp = Path(directory)
        program = temp / "four team LAN event"
        if executable:
            executable = Path(executable).resolve()
            shutil.copytree(executable.parent, program)
            config = program / "final-four-lan.json"
            assert not config.exists(), "package must start with a fresh four-team configuration"
            # Exercise the actual double-click entry point, including spaces in its path.
            starter = program / "Start-FinalFour-LAN-8080.cmd"
            command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "call", str(starter), "--no-browser"]
            original_config = None
        else:
            program.mkdir()
            settings = defaults()
            for key in ("katago_bin", "katago_model", "katago_config", "katago_gpu_bin", "katago_gpu_config"):
                settings[key] = str(ROOT / settings[key])
            settings.update(data_dir=str(program / "data"), open_browser=False, host="0.0.0.0", port=8080)
            config = program / "final-four-lan.json"
            original_config = json.dumps(settings)
            config.write_text(original_config, encoding="utf-8")
            command = [sys.executable, str(ROOT / "launcher.py"), "--final-four-lan", "--config", str(config), "--no-browser", "--no-pause"]
        if explicit_port is not None:
            command += ["--port", str(port)]
        originals = {}
        for name in ("data", "duel-data", "knockout-data", "final-four-data"):
            folder = program / name
            folder.mkdir()
            marker = folder / "tournament.sqlite3"
            marker.write_bytes((name + " must remain untouched").encode())
            originals[marker] = marker.read_bytes()
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
                    with httpx.Client(base_url=f"http://{address}:{port}", trust_env=False, timeout=30) as client:
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
                        if original_config is None:
                            original_config = config.read_text(encoding="utf-8")
                            assert json.loads(original_config)["port"] == 8080
                        assert (program / "final-four-lan-data/tournament.sqlite3").exists()
                        assert client.get("/").status_code == client.get("/static/knockout.js").status_code == 200
                        admin_token = (program / "final-four-lan-data/admin-token").read_text().strip()
                        host_auth = {"Authorization": "Bearer " + admin_token}
                        network = client.get("/api/admin/network", headers=host_auth).json()
                        assert network["bind_host"] == "0.0.0.0" and network["port"] == port and network["lan_enabled"]
                        assert "duel" not in client.get("/api/state").json()

                        def post(path, body, token=None):
                            token = token or (admin_token if path.startswith(("/api/admin/", "/api/knockout/")) else None)
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
                            ongoing = next((g for g in match(ident)["games"] if g["status"] != "finished"), None)
                            game = client.get(f"/api/games/{ongoing['id']}").json() if ongoing else post(f"/api/knockout/matches/{ident}/next", options)
                            winner = winner or game["seats"]["B"]["team"]
                            post(f"/api/admin/games/{game['id']}/action", {"action": "adjudicate", "winner": winner})
                            return game, winner

                        if launch == 0:
                            roster = client.get("/api/admin/state", headers=host_auth).json()["teams"]
                            assert [team["name"] for team in roster] == [entry[2] for entry in INITIAL_ROSTER]
                            assert [team["play_code"] for team in roster] == list(INITIAL_CODES)
                            selected = [roster[index]["id"] for index in (0, 1, 10, 15)]
                            names = [team["name"] for team in roster]
                            issued = {team["id"]: team["play_code"] for team in roster}
                            body = {"team_ids": selected, "board_size": 9, "komi": 7.5, "visits": 32, "rules_confirmed": True}
                            assert client.post("/api/knockout/setup", json=body).status_code == 401
                            post("/api/knockout/setup", body)
                            post("/api/knockout/matches/semi-2/next", {})
                            game = post("/api/knockout/matches/semi-1/next", {})
                            gid = game["id"]
                            credentials = {seat["team"]: issued[seat["team"]] for seat in game["seats"].values()}
                            public = client.get("/api/state").text
                            assert all(code not in public for code in issued.values())
                            assert client.get(f"/api/games/{gid}/seat", headers={"Authorization":"Bearer WRONG"}).status_code == 403
                            assert client.get(f"/api/games/{gid}/seat", headers={"Authorization":"Bearer "+INITIAL_CODES[2]}).status_code == 403
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
                            post(f"/api/admin/games/{gid}/action", {"action": "pause", "reason": "四強版重啟檢查"})
                        else:
                            game = next(g for g in state()["games"] if g["id"] == gid)
                            assert game["id"] == gid and game["board"] == saved_board and game["status"] == "paused"
                            assert [team["name"] for team in state()["teams"]] == names
                            post(f"/api/admin/games/{gid}/action", {"action": "resume"})
                            game = wait_game(gid)
                            for _ in range(2):
                                token = credentials[game["seats"][game["turn"]]["team"]]
                                game = post(f"/api/games/{gid}/move", {"version": game["version"], "move": "pass"}, token)
                                if game["status"] != "finished":
                                    game = wait_game(gid)
                            assert game["result"]["reason"] == "score" and match("semi-1")["winner"]
                            assert "KM[7.5]" in client.get(f"/api/games/{gid}/sgf").text
                            a = game["result"]["winner"]
                            assert match("semi-1")["winner"]["id"] == a
                            assert match("semi-1")["best_of"] == 1
                            assert client.post("/api/knockout/matches/semi-1/next", headers=host_auth, json={}).status_code == 409
                            _, c = host_win("semi-2")
                            assert match("semi-2")["winner"]["id"] == c
                            assert client.post("/api/knockout/matches/semi-2/next", headers=host_auth, json={}).status_code == 409
                            final, _ = host_win("final-1", c, device_a=True)
                            assert final["remaining"] == {"B": 300, "W": 600}
                            assert client.get("/api/bracket").json()["champion"] is None
                            host_win("final-1", c)
                            assert client.get("/api/bracket").json()["champion"]["id"] == c
                            assert client.post("/api/knockout/matches/final-1/next", headers=host_auth, json={}).status_code == 409
                            exported = client.get("/api/admin/export", headers=host_auth).json()
                            assert len(exported["games"]) == 4 and exported["awards"] == []
                            assert all("token" not in seat for game in exported["games"] for seat in game["seats"].values())
                        assert all(marker.read_bytes() == original for marker, original in originals.items())
                        assert config.read_text(encoding="utf-8") == original_config
                except Exception:
                    log.flush(); log.seek(0); print(log.read()[-3500:])
                    raise
                finally:
                    stop(process)
        report = {"passed": True, "packaged_app": bool(executable), "platform": sys.platform,
                  "port": port, "default_port": explicit_port is None, "lan_address": address, "bind_host": "0.0.0.0",
                  "entrypoint": "Windows CMD" if executable else "source",
                  "fresh_configuration_port": 8080, "isolated_final_four_data": True,
                  "real_ai_games": 1, "other_games": "organizer adjudication to verify advancement",
                  "builtin_eighteen_team_dropdown": True, "backend_team_codes": True, "simultaneous_semifinals": True, "semifinal_bo1": True, "final_bo3": True,
                  "final_ten_five_minutes": True,
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
