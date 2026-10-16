"""Real-AI LAN room cap, computer/PvP play and formal-data isolation check."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(executable=None, port=8774):
    with tempfile.TemporaryDirectory(prefix="yanghai-practice-lan-") as directory:
        temp = Path(directory)
        formal = temp / "data"
        formal.mkdir()
        marker = formal / "tournament.sqlite3"
        before = b"formal tournament must never be opened by LAN practice"
        marker.write_bytes(before)
        if executable:
            executable = Path(executable).resolve()
            bundle = executable.parent
            settings = json.loads((bundle / "tournament.example.json").read_text())
            command = [str(executable)]
            if os.name == "nt":
                for name in ("Start-Practice.cmd", "Start-Practice-LAN.cmd"):
                    assert "--practice-lan --no-pause" in (bundle / name).read_text()
                assert "--practice --no-pause" in (bundle / "Start-Practice-Offline.cmd").read_text()
        else:
            from launcher import defaults
            settings = defaults()
            bundle = ROOT
            command = [sys.executable, str(ROOT / "launcher.py")]
        for field in ("katago_bin", "katago_model", "katago_config"):
            settings[field] = str(bundle / settings[field])
        settings.update(host="127.0.0.1", data_dir=str(formal), open_browser=False)
        config = temp / "tournament.json"
        original = json.dumps(settings)
        config.write_text(original)
        command += ["--config", str(config), "--practice-lan", "--port", str(port), "--no-browser", "--no-pause"]
        environment = os.environ.copy()
        for name in ("KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "AI_CONCURRENCY", "TOURNAMENT_DATA"):
            environment.pop(name, None)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            environment[name] = "http://127.0.0.1:1"
        with (temp / "server.log").open("w+") as log:
            process = subprocess.Popen(command, cwd=temp, env=environment, stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as local:
                    for _ in range(180):
                        assert process.poll() is None, "practice lobby exited early"
                        try:
                            health = local.get("/api/health")
                            if health.status_code == 200:
                                assert health.json()["ai_ready"] and health.json()["mode"] == "practice-lan"
                                break
                        except httpx.ConnectError:
                            pass
                        time.sleep(.25)
                    else:
                        raise AssertionError("practice lobby not ready")
                    urls = local.get("/api/practice-lan/network").json()["urls"]
                    assert urls, "no LAN interface available for validation"
                with httpx.Client(base_url=urls[0], trust_env=False, timeout=10) as client:
                    for asset in ("/", "/static/practice-lobby.js", "/static/style.css"):
                        assert client.get(asset).status_code == 200
                    assert client.get("/api/state").status_code == client.get("/api/admin/state").status_code == 404

                    def create(**body):
                        response = client.post("/api/practice-lan/rooms", json={"board_size": 9, **body})
                        assert response.status_code == 200
                        return response.json()

                    def endpoint(room, action=""):
                        return "/api/practice-lan/rooms/" + room["game"]["id"] + ("/" + action if action else "")

                    def auth(room):
                        return {"Authorization": "Bearer " + room["token"]}

                    def ready(room, moves):
                        for _ in range(1200):
                            game = client.get(endpoint(room), headers=auth(room)).json()["game"]
                            assert game["status"] != "error", "real AI room analysis failed"
                            if game["status"] == "playing" and len(game["moves"]) == moves:
                                return game
                            time.sleep(.05)
                        raise AssertionError("practice room analysis did not complete")

                    human = create(name="LAN 真人檢查")
                    joined = client.post(endpoint(human, "join"), json={"player_name": "對手"})
                    assert joined.status_code == 200
                    guest = joined.json()
                    game = ready(human, 0)
                    assert client.post(endpoint(human, "move"), headers=auth(guest), json={"version": game["version"], "move": "D4"}).status_code == 403
                    assert client.post(endpoint(human, "move"), headers=auth(human), json={"version": game["version"], "move": game["forbidden"]}).status_code == 400
                    for participant, count in ((human, 1), (guest, 2)):
                        response = client.post(endpoint(human, "move"), headers=auth(participant), json={"version": game["version"], "move": game["candidates"][1]["move"]})
                        assert response.status_code == 200
                        game = ready(human, count)
                    assert "KM[7.5]" in client.get(endpoint(human, "sgf")).text
                    computer = create(mode="ai", human_color="W", name="LAN 電腦檢查")
                    game = ready(computer, 1)
                    assert game["turn"] == "W" and game["moves"][0][0] == "B"
                    response = client.post(endpoint(computer, "move"), headers=auth(computer), json={"version": game["version"], "move": game["candidates"][1]["move"]})
                    assert response.status_code == 200
                    game = ready(computer, 3)
                    assert game["turn"] == "W" and [m[0] for m in game["moves"]] == ["B", "W", "B"]
                    assert client.post(endpoint(computer, "undo"), headers=auth(computer), json={"version": game["version"]}).status_code == 200
                    assert ready(computer, 1)["turn"] == "W"
                    local_room = create(mode="local")
                    assert ready(local_room, 0)["viewer"]["color"] == "both"
                    waiting = [create(name=f"空桌檢查{i}") for i in range(12)]
                    assert client.get("/api/practice-lan/rooms").json()["tables"] == 15
                    assert client.post("/api/practice-lan/rooms", json={}).status_code == 429

                    def poll(_):
                        with httpx.Client(base_url=urls[0], trust_env=False, timeout=10) as browser:
                            response = browser.get("/api/practice-lan/rooms")
                            assert response.status_code == 200 and response.json()["tables"] == 15
                            assert all(r["token"] not in response.text for r in [human, guest, computer, local_room, *waiting])
                            return True
                    with ThreadPoolExecutor(max_workers=15) as executor:
                        assert all(executor.map(poll, range(50)))
                    closed = waiting[0]
                    assert client.request("DELETE", endpoint(closed), headers=auth(human), json={"version": 0}).status_code == 403
                    assert client.request("DELETE", endpoint(closed), headers=auth(closed), json={"version": 0}).status_code == 200
                    create(name="名額回收檢查")
                    assert client.get("/api/practice-lan/rooms").json()["tables"] == 15
                assert marker.read_bytes() == before and config.read_text() == original
                assert not (formal / "admin-token").exists() and not (formal / "backups").exists()
                assert not list((temp / "practice-data").rglob("*.sqlite3"))
                report = {"passed": True, "real_ai": True, "lan_verified": True, "max_tables": 15,
                          "sixteenth_table_rejected": True, "slot_release_verified": True,
                          "pvp_turns_and_auth": True, "automatic_computer_moves_and_undo": True,
                          "local_two_person_room": True, "simulated_clients": 50,
                          "credentials_hidden": True, "formal_data_untouched": True,
                          "packaged_app": bool(executable), "platform": sys.platform}
                (ROOT / ".runtime").mkdir(exist_ok=True)
                (ROOT / ".runtime/practice-lan-smoke-report.json").write_text(json.dumps(report, indent=2) + "\n")
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
    parser.add_argument("--port", type=int, default=8774)
    args = parser.parse_args()
    main(args.executable, args.port)
