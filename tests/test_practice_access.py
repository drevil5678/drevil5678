import time

from fastapi.testclient import TestClient

from server.app import create_app
from tests.test_app import TestEngine

BASE = "/api/local-practice/rooms"


def setup(client, app, enabled=True):
    headers = {"Authorization": "Bearer " + app.state.store.admin_token}
    response = client.post("/api/admin/teams", headers=headers,
                           json={"name": "練習隊", "players": ["隊長", "隊員"]})
    assert response.status_code == 200
    team = response.json()
    assert client.post("/api/admin/practice", headers=headers, json={"enabled": enabled}).status_code == 200
    return headers, team


def login(client, code):
    return client.post("/api/local-practice/login", json={"code": code})


def wait_game(client, ident, expected="playing"):
    for _ in range(200):
        game = client.get(BASE + "/" + ident).json()["game"]
        if game["status"] == expected:
            return game
        time.sleep(.01)
    raise AssertionError(game["status"])


def test_auth_is_backend_enforced_and_practice_defaults_closed(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        assert client.get("/api/local-practice/access").json() == {"enabled": False, "authenticated": False, "team": None}
        assert client.post("/api/admin/practice", json={"enabled": True}).status_code == 401
        headers, team = setup(client, app, enabled=False)
        assert login(client, team["play_code"]).status_code == 403
        assert client.get(BASE).status_code == client.post(BASE, json={}).status_code == 401
        assert client.post("/api/admin/practice", headers=headers, json={"enabled": True}).status_code == 200
        assert login(client, "wrong-code").status_code == 401
        normalized = team["play_code"].replace("-", "").lower()
        response = login(client, normalized)
        assert response.status_code == 200 and "httponly" in response.headers["set-cookie"].lower()
        assert client.get("/api/local-practice/access").json()["team"] == {"id": team["id"], "name": team["name"]}
        assert client.get(BASE).status_code == 200
        assert client.post("/api/local-practice/logout", json={}).status_code == 200
        assert client.get(BASE).status_code == 401
        assert login(client, team["play_code"]).status_code == 200
        assert client.post("/api/local-practice/login", json={"code": team["play_code"]},
                           headers={"Origin": "https://evil.example"}).status_code == 403


def test_closing_stops_mutations_but_preserves_board_and_sgf(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        headers, team = setup(client, app)
        assert login(client, team["play_code"]).status_code == 200
        created = client.post(BASE, json={"board_size": 9, "forbid_best": False}).json()["game"]
        path = BASE + "/" + created["id"]
        game = wait_game(client, created["id"])
        assert client.post(path + "/move", json={"version": game["version"], "move": "D4"}).status_code == 200
        game = wait_game(client, created["id"])
        board = game["board"]
        assert client.post("/api/admin/practice", headers=headers, json={"enabled": False}).status_code == 200
        paused = client.get(path).json()["game"]
        assert paused["status"] == "paused" and paused["board"] == board
        assert client.post(BASE, json={}).status_code == 403
        for action in ("move", "undo", "retry", "finish"):
            body = {"version": paused["version"], **({"move": "pass"} if action == "move" else {})}
            assert client.post(path + "/" + action, json=body).status_code == 403
        assert client.get(path + "/sgf").status_code == 200
        assert client.post("/api/local-practice/logout", json={}).status_code == 409
        assert client.post("/api/admin/practice", headers=headers, json={"enabled": True}).status_code == 200
        resumed = wait_game(client, created["id"])
        assert resumed["board"] == board and len(resumed["moves"]) == 1
        assert client.post("/api/admin/practice", headers=headers, json={"enabled": False}).status_code == 200
        paused = client.get(path).json()["game"]
        assert client.request("DELETE", path, json={"version": paused["version"]}).status_code == 200


def test_closing_during_computer_analysis_prevents_late_move_and_reopen_continues(tmp_path):
    engine = TestEngine()
    engine.delay = .15
    app = create_app(tmp_path, engine=engine)
    with TestClient(app) as client:
        headers, team = setup(client, app)
        assert login(client, team["play_code"]).status_code == 200
        game = client.post(BASE, json={"board_size": 9, "mode": "ai", "human_color": "W"}).json()["game"]
        path = BASE + "/" + game["id"]
        assert client.post("/api/admin/practice", headers=headers, json={"enabled": False}).status_code == 200
        time.sleep(.25)
        paused = client.get(path).json()["game"]
        assert paused["moves"] == [] and paused["status"] == "paused"
        assert client.post("/api/admin/practice", headers=headers, json={"enabled": True}).status_code == 200
        resumed = wait_game(client, game["id"])
        assert len(resumed["moves"]) == 1 and resumed["turn"] == "W"


def test_code_rotation_revokes_existing_practice_session(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        headers, team = setup(client, app)
        assert login(client, team["play_code"]).status_code == 200
        assert client.get(BASE).status_code == 200
        response = client.post(f"/api/admin/teams/{team['id']}/code", headers=headers, json={"reason": "換碼"})
        assert response.status_code == 200
        assert client.get(BASE).status_code == 401
        assert not client.get("/api/local-practice/access").json()["authenticated"]
        assert login(client, team["play_code"]).status_code == 401
        assert login(client, response.json()["play_code"]).status_code == 200


def test_practice_open_setting_survives_restart_but_browser_must_reauthenticate(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        _, team = setup(client, app)
        assert login(client, team["play_code"]).status_code == 200
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        assert client.get("/api/state").json()["practice_enabled"] is True
        assert client.get(BASE).status_code == 401
        assert login(client, team["play_code"]).status_code == 200
