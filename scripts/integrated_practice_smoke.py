"""Check same-port, same-browser practice with real AI in source or Windows builds."""
import argparse
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BASE = "/api/local-practice/rooms"


def unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def listener_ports(process):
    """Inspect only this isolated launcher's process tree, never other services."""
    parent = psutil.Process(process.pid)
    ports = set()
    for owned in [parent, *parent.children(recursive=True)]:
        try:
            ports.update(c.laddr.port for c in owned.net_connections(kind="tcp")
                         if c.status == psutil.CONN_LISTEN)
        except psutil.NoSuchProcess:
            pass
    return ports


def stop(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        # Only the launcher's captured descendants belong to this test.
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for owned in [*children, psutil.Process(process.pid)]:
            try:
                owned.kill()
            except psutil.NoSuchProcess:
                pass
        process.wait(timeout=5)


def main(executable=None, port=None):
    port = port or unused_port()
    assert port != 8001, "choose a port other than the obsolete standalone practice port"
    with tempfile.TemporaryDirectory(prefix="yanghai-integrated-practice-") as directory:
        temp = Path(directory)
        data = temp / "data"
        if executable:
            executable = Path(executable).resolve()
            bundle = executable.parent
            settings = json.loads((bundle / "tournament.example.json").read_text(encoding="utf-8-sig"))
            command = [str(executable)]
        else:
            from launcher import defaults
            bundle = ROOT
            settings = defaults()
            command = [sys.executable, str(ROOT / "launcher.py")]
        for field in ("katago_bin", "katago_model", "katago_config", "katago_gpu_bin", "katago_gpu_config"):
            settings[field] = str(bundle / settings[field])
        # --practice-lan must use the configured tournament port and same database.
        settings.update(host="127.0.0.1", port=port, data_dir=str(data), open_browser=False)
        config = temp / "tournament.json"
        original = json.dumps(settings)
        config.write_text(original, encoding="utf-8")
        command += ["--config", str(config), "--practice-lan", "--port", str(port),
                    "--empty-roster", "--no-browser", "--no-pause"]
        environment = os.environ.copy()
        for key in ("KATAGO_BIN", "KATAGO_MODEL", "KATAGO_CONFIG", "AI_CONCURRENCY", "TOURNAMENT_DATA", "KATAGO_ENGINE_MODE", "KATAGO_GPU_BIN", "KATAGO_GPU_CONFIG", "AI_GPU_CONCURRENCY", "KATAGO_GPU_DEVICE"):
            environment.pop(key, None)
        # Prove all inference runs from local model/binary files.
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            environment[key] = "http://127.0.0.1:1"
        with (temp / "server.log").open("w+", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=temp, env=environment, stdout=log, stderr=log)
            try:
                url = f"http://127.0.0.1:{port}"
                with ExitStack() as contexts, httpx.Client(base_url=url, trust_env=False, timeout=10) as client, \
                        httpx.Client(base_url=url, trust_env=False, timeout=10) as foreign:
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        assert process.poll() is None, "integrated launcher exited before readiness"
                        try:
                            response = client.get("/api/health")
                            if response.status_code == 200:
                                assert response.json()["ai_ready"], "real KataGo not ready"
                                break
                        except (httpx.ConnectError, httpx.TimeoutException):
                            pass
                        time.sleep(.1)
                    else:
                        raise AssertionError("integrated launcher never became ready")
                    assert listener_ports(process) == {port}, "launcher started an extra listening port"
                    index = client.get("/")
                    assert index.status_code == 200 and 'data-view="practice"' in index.text
                    assert 'data-src="/local-practice"' in index.text
                    for path in ("/local-practice", "/static/local-practice.js", "/static/style.css", "/api/state"):
                        assert client.get(path).status_code == 200, path
                    assert "frame-ancestors 'self'" in client.get("/local-practice").headers["content-security-policy"]
                    assert client.get("/api/practice-lan/rooms").status_code == 404, "obsolete remote practice API enabled"
                    token = (data / "admin-token").read_text(encoding="utf-8").strip()
                    admin = {"Authorization": "Bearer " + token}
                    network = client.get("/api/admin/network", headers=admin).json()
                    assert network["port"] == port and network["bind_host"] == "0.0.0.0"
                    assert network["interfaces"], "no local LAN interface for source-IP isolation check"
                    assert client.get(BASE).status_code == 401
                    assert client.post("/api/admin/practice", headers=admin, json={"enabled": True}).status_code == 200
                    for n in range(2):
                        response = client.post("/api/admin/teams", headers=admin, json={
                            "name": f"隔離驗證隊{n+1}", "players": [f"隊長{n+1}", f"隊員{n+1}"]})
                        assert response.status_code == 200
                    practice_code = response.json()["play_code"]
                    assert client.post("/api/admin/settings", headers=admin, json={"board_size": 9, "rules_confirmed": True}).status_code == 200
                    assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
                    formal_before = client.get("/api/admin/state", headers=admin).json()
                    public_before = client.get("/api/state").json()
                    assert len(formal_before["games"]) == 1 and formal_before["games"][0]["status"] == "ready"
                    assert client.post(BASE, json={}).status_code == 401, "room creation accepted without team authentication"
                    login = client.post("/api/local-practice/login", json={"code": practice_code})
                    assert login.status_code == 200
                    initial = client.get(BASE)
                    assert initial.json()["tables"] == 0
                    cookie_header = login.headers["set-cookie"].lower()
                    assert "httponly" in cookie_header and "samesite=strict" in cookie_header
                    assert client.post(BASE, json={"mode": "pvp"}).status_code == 400
                    assert client.post(BASE, json={}, headers={"Origin": "http://another.example"}).status_code == 403
                    assert client.post(BASE, json={"board_size": 10}).status_code == 400

                    def endpoint(room, action=""):
                        return BASE + "/" + room["game"]["id"] + ("/" + action if action else "")

                    def create(browser=client, **body):
                        response = browser.post(BASE, json={"board_size": 9, **body})
                        assert response.status_code == 200, f"room create: HTTP {response.status_code}"
                        return response.json()

                    def ready(room, moves=None, browser=client):
                        deadline = time.monotonic() + 90
                        while time.monotonic() < deadline:
                            response = browser.get(endpoint(room))
                            assert response.status_code == 200, "owned room inaccessible"
                            game = response.json()["game"]
                            assert game["status"] != "error", "real AI practice analysis failed"
                            if game["status"] in ("playing", "finished") and (moves is None or len(game["moves"]) == moves):
                                return game
                            time.sleep(.04)
                        raise AssertionError("real practice AI did not finish analysis")

                    def action(room, action_name, game, browser=client, **body):
                        response = browser.post(endpoint(room, action_name), json={"version": game["version"], **body})
                        assert response.status_code == 200, f"{action_name}: HTTP {response.status_code}"
                        return response.json()["game"]

                    def close(room, browser=client):
                        for _ in range(10):
                            response = browser.get(endpoint(room))
                            assert response.status_code == 200
                            response = browser.request("DELETE", endpoint(room), json={"version": response.json()["game"]["version"]})
                            if response.status_code == 200:
                                return
                            assert response.status_code == 409
                        raise AssertionError("table could not be closed after repeated version refreshes")

                    local_room = create(mode="local", forbid_best=False)
                    game = ready(local_room, 0)
                    game = action(local_room, "move", game, move="pass")
                    game = ready(local_room, 1)
                    assert game["turn"] == "W" and game["move_number"] == 2
                    game = action(local_room, "move", game, move="pass")
                    assert game["status"] == "finished" and game["moves_played"] == 2
                    assert game["result"]["reason"] == "double_pass" and game["result"]["winner"] == "W"
                    assert game["result"]["score"]["black"] == 0 and game["result"]["score"]["white"] == 7.5
                    assert "KM[7.5]" in client.get(endpoint(local_room, "sgf")).text
                    assert client.post(BASE, json={}).status_code == 409
                    close(local_room)

                    computer = create(mode="ai", human_color="W")
                    game = ready(computer, 1)
                    assert game["turn"] == "W" and game["moves"][0][0] == "B"
                    assert client.post(endpoint(computer, "move"), json={"version": game["version"], "move": game["forbidden"]}).status_code == 400
                    chosen = next(h["move"] for h in game["candidates"] if h["move"] != game["forbidden"] and h["move"] != "pass")
                    action(computer, "move", game, move=chosen)
                    game = ready(computer, 3)
                    assert game["turn"] == "W" and [m[0] for m in game["moves"]] == ["B", "W", "B"]
                    action(computer, "undo", game)
                    assert ready(computer, 1)["turn"] == "W"

                    # A different browser sees the global count but no other player's tables.
                    assert foreign.post("/api/local-practice/login", json={"code": practice_code}).status_code == 200
                    assert foreign.get(BASE).json()["rooms"] == []
                    assert foreign.get(endpoint(computer)).status_code == 404
                    assert foreign.get(endpoint(computer, "sgf")).status_code == 404
                    assert foreign.post(endpoint(computer, "join"), json={}).status_code == 404
                    assert foreign.post(endpoint(computer, "finish"), json={"version": game["version"]}).status_code == 404
                    stranger = create(foreign, mode="local")
                    own = client.get(BASE).json()
                    assert len(own["rooms"]) == 1 and own["tables"] == 2
                    assert stranger["game"]["id"] not in {r["id"] for r in own["rooms"]}
                    # Even copying the HttpOnly cookie cannot admit a different source IP.
                    address = network["interfaces"][0]["address"]
                    cookies = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
                    with httpx.Client(trust_env=False, timeout=10) as another_computer:
                        response = another_computer.get(f"http://{address}:{port}" + endpoint(computer), headers={"Cookie": cookies})
                        assert response.status_code == 404, "different source IP admitted with copied browser cookie"

                    # Repeated requests/tabs cannot bypass the one-browser-one-table rule.
                    with ThreadPoolExecutor(max_workers=8) as executor:
                        duplicates = list(executor.map(lambda _: client.post(BASE, json={}), range(20)))
                    assert all(r.status_code == 409 for r in duplicates)
                    assert len(client.get(BASE).json()["rooms"]) == 1
                    # Capacity remains protected across distinct browsers, without a public numeric message.
                    browsers = [contexts.enter_context(httpx.Client(base_url=url, trust_env=False, timeout=10)) for _ in range(20)]
                    for browser in browsers:
                        assert browser.post("/api/local-practice/login", json={"code": practice_code}).status_code == 200
                        browser.get(BASE)
                    with ThreadPoolExecutor(max_workers=8) as executor:
                        attempts = list(executor.map(lambda browser: browser.post(BASE, json={"board_size": 9}), browsers))
                    assert [r.status_code for r in attempts].count(200) == 13
                    assert [r.status_code for r in attempts].count(429) == 7
                    assert all("15" not in r.text for r in attempts if r.status_code == 429)
                    waiting = [(browser, r.json()) for browser, r in zip(browsers, attempts) if r.status_code == 200]
                    available_browser = next(browser for browser, r in zip(browsers, attempts) if r.status_code == 429)
                    assert client.get(BASE).json()["tables"] == 15
                    assert foreign.post(BASE, json={}).status_code == 409

                    def isolated_browser(_):
                        with httpx.Client(base_url=url, trust_env=False, timeout=10) as browser:
                            assert browser.post("/api/local-practice/login", json={"code": practice_code}).status_code == 200
                            response = browser.get(BASE)
                            assert response.status_code == 200
                            assert response.json()["tables"] == 15 and response.json()["rooms"] == []
                            assert browser.get(endpoint(computer)).status_code == 404
                        return True
                    with ThreadPoolExecutor(max_workers=10) as executor:
                        assert all(executor.map(isolated_browser, range(50)))
                    assert listener_ports(process) == {port}, "practice opened another listening port"
                    released_browser, released_room = waiting.pop()
                    close(released_room, released_browser)
                    replacement = create(available_browser, name="收桌名額回收")
                    assert client.get(BASE).json()["tables"] == 15
                    close(computer)
                    for browser, room in [*waiting, (available_browser, replacement)]:
                        close(room, browser)
                    close(stranger, foreign)
                    assert client.get(BASE).json()["tables"] == 0
                    assert client.get("/api/admin/state", headers=admin).json() == formal_before, "practice changed formal tournament state"
                    assert client.get("/api/state").json() == public_before, "practice changed public formal results"
                    assert config.read_text(encoding="utf-8") == original, "launcher rewrote existing configuration"
                    assert not (temp / "practice-data").exists(), "launcher still split practice onto separate server/data path"
                    assert listener_ports(process) == {port} and 8001 not in listener_ports(process)
                report = {
                    "passed": True, "real_ai": True, "same_port": port, "second_port_8001": False,
                    "shared_formal_and_practice_routes": True, "max_tables": 15,
                    "team_code_authentication": True, "anonymous_access_denied": True, "practice_opened_by_admin": True,
                    "concurrent_global_capacity_verified": True, "slot_release_verified": True,
                    "one_table_per_browser_atomic": True,
                    "same_computer_two_human": True, "automatic_computer_moves_and_undo": True,
                    "double_pass_current_board_score": True, "visible_move_count_api": True,
                    "httponly_cookie_and_source_ip_bound": True, "own_tables_only": True,
                    "remote_pvp_and_join_disabled": True, "simulated_clients": 50,
                    "formal_state_untouched": True, "configuration_preserved": True,
                    "packaged_app": bool(executable), "platform": sys.platform,
                }
                (ROOT / ".runtime").mkdir(exist_ok=True)
                (ROOT / ".runtime/integrated-practice-smoke-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(report, indent=2))
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read()[-3500:])
                raise
            finally:
                stop(process)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, help="test a frozen launcher rather than source")
    parser.add_argument("--port", type=int, help="otherwise choose an unused port automatically")
    args = parser.parse_args()
    main(args.executable, args.port)
