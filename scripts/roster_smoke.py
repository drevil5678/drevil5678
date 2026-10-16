"""Exercise default 18-team startup and restart, with isolated data and real AI."""
import argparse
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
from server.initial_roster import INITIAL_CODES, INITIAL_ROSTER


def main(executable=None, port=8772):
    with tempfile.TemporaryDirectory(prefix="yanghai-roster-") as directory:
        temp = Path(directory)
        data = temp / "data"
        if executable:
            executable = Path(executable).resolve()
            bundle = executable.parent
            settings = json.loads((bundle / "tournament.example.json").read_text())
            for field in ("katago_bin", "katago_model", "katago_config"):
                settings[field] = str(bundle / settings[field])
            command = [str(executable)]
        else:
            from launcher import defaults
            settings = defaults()
            for field in ("katago_bin", "katago_model", "katago_config"):
                settings[field] = str(ROOT / settings[field])
            command = [sys.executable, str(ROOT / "launcher.py")]
        settings.update(host="127.0.0.1", port=port, data_dir=str(data), open_browser=False)
        config = temp / "tournament.json"
        original = json.dumps(settings)
        config.write_text(original)
        command += ["--config", str(config), "--no-browser", "--no-pause"]
        environment = os.environ.copy()
        for key in ("KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "AI_CONCURRENCY", "TOURNAMENT_DATA"):
            environment.pop(key, None)

        def stop(process):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    process.terminate()
                process.wait(timeout=20)

        with (temp / "server.log").open("w+") as log:
            for launch in range(2):
                process = subprocess.Popen(command, cwd=temp, env=environment, stdout=log, stderr=log)
                try:
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
                        deadline = time.monotonic() + 45
                        while time.monotonic() < deadline:
                            assert process.poll() is None, "default launcher exited"
                            try:
                                health = client.get("/api/health")
                                if health.status_code == 200:
                                    assert health.json()["ai_ready"], "real KataGo not ready"
                                    break
                            except httpx.ConnectError:
                                pass
                            time.sleep(.25)
                        else:
                            raise AssertionError("default launcher did not become ready")
                        token = (data / "admin-token").read_text().strip()
                        auth = {"Authorization": "Bearer " + token}
                        state = client.get("/api/admin/state", headers=auth).json()
                        teams = state["teams"]
                        assert len(teams) == 18 and not state["rounds"] and not state["games"]
                        assert all(t["checked_in"] and t["eligibility_checked"] for t in teams)
                        if launch == 0:
                            assert [(t["players"][0], t["players"][1], t["name"]) for t in teams] == list(INITIAL_ROSTER)
                            issued = {t["id"]: t["play_code"] for t in teams}
                            assert len(set(issued.values())) == 18
                            assert [t["play_code"] for t in teams] == list(INITIAL_CODES)
                            first_token = token
                            edited = {key: teams[1][key] for key in ("name", "players", "mars_points")}
                            edited["name"] = "重啟保留修改測試"
                            assert client.put(f"/api/admin/teams/{teams[1]['id']}", headers=auth, json=edited).status_code == 200
                        else:
                            assert {t["id"]: t["play_code"] for t in teams} == issued
                            assert teams[1]["name"] == edited["name"] and token == first_token
                            assert len([e for e in state["events"] if e["kind"] == "initial_roster"]) == 1
                        public = client.get("/api/state").text
                        assert all(t["play_code"] not in public and t["players"][0] not in public for t in teams)
                        assert client.get("/api/admin/state").status_code == 401
                        assert config.read_text() == original
                except Exception:
                    log.flush()
                    log.seek(0)
                    print(log.read()[-3000:])
                    raise
                finally:
                    stop(process)
        report = {"passed": True, "teams": 18, "launches": 2, "ai": "real KataGo",
                  "static_codes_ids_and_admin_key_preserved": True, "organizer_edit_preserved": True,
                  "automatic_check_in_without_automatic_games": True, "public_credentials_and_players_hidden": True,
                  "packaged_app": bool(executable), "platform": sys.platform}
        (ROOT / ".runtime").mkdir(exist_ok=True)
        (ROOT / ".runtime/roster-smoke-report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--port", type=int, default=8772)
    args = parser.parse_args()
    main(args.executable, args.port)
