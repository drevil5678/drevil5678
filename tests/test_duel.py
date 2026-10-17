from fastapi.testclient import TestClient

from server.app import create_app
from tests.test_app import TestEngine, seed_sixty, start, wait_status


def create(client, **changes):
    return client.post("/api/duel/new", json={
        "team_a": "甲隊", "team_b": "乙隊", "board_size": 9,
        "visits": 16, "rules_confirmed": True, **changes,
    })


def test_duel_routes_and_credentials_are_local_only(tmp_path):
    with TestClient(create_app(tmp_path / "event", engine=TestEngine())) as event:
        assert event.get("/api/duel/export").status_code == 404
        assert "duel" not in event.get("/api/state").json()
    app = create_app(tmp_path / "duel", engine=TestEngine(), bootstrap=True, duel=True)
    with TestClient(app) as client:
        assert not client.get("/api/state").json()["teams"]
        assert create(client).status_code == 200
        state = client.get("/api/state").json()
        assert state["duel"]["credentials"][state["games"][0]["id"]]
        assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 403
        assert create(client, team_b="丙隊").status_code == 409
        assert len(app.state.store.state["teams"]) == 2
        assert client.post("/api/duel/new", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    with TestClient(create_app(tmp_path / "remote", engine=TestEngine(), duel=True),
                    client=("192.168.1.42", 1234)) as client:
        assert client.get("/api/state").status_code == 403
        assert create(client).status_code == 403


def test_duel_uses_formal_dice_clocks_and_double_pass_scoring(tmp_path):
    app = create_app(tmp_path, engine=TestEngine(), duel=True)
    with TestClient(app) as client:
        assert create(client, championship=True, device_a=True).status_code == 200
        state = app.state.store.state
        game = state["games"][0]
        header = {"Authorization": "Bearer " + app.state.store.admin_token}
        assert game["remaining"] == {"B": 600, "W": 900}
        public = start(client, header, game)
        assert public["pregame"]["winner"] and all(public["pregame"]["ready"].values())
        for _ in range(2):
            token = game["seats"][public["turn"]]["token"]
            response = client.post(f"/api/games/{game['id']}/move", headers={"Authorization": "Bearer " + token},
                                   json={"version": public["version"], "move": "pass"})
            assert response.status_code == 200
            public = response.json()
            if public["status"] != "finished":
                public = wait_status(client, game["id"])
        assert public["result"] == {"winner": game["seats"]["W"]["team"], "reason": "score", "points": 1}
        assert public["score"]["white"] == 7.5
        exported = client.get("/api/duel/export")
        assert "duel-records.json" in exported.headers["content-disposition"]
        saved = exported.json()
        assert saved["awards"] == [] and saved["games"][0]["stage"] == "special"
        assert len(saved["games"][0]["moves"]) == 2
        assert "token" not in saved["games"][0]["seats"]["B"]
        assert "play_code" not in saved["teams"][0]
        assert "KM[7.5]" in client.get(f"/api/games/{game['id']}/sgf").text
        assert create(client, komi=6.5).status_code == 409
        assert create(client, team_a="丙隊", team_b="丁隊").status_code == 200
        assert len(state["games"]) == 2 and state["games"][0]["result"]


def test_duel_threshold_is_same_inclusive_40_percent_rule(tmp_path):
    engine = TestEngine()
    app = create_app(tmp_path, engine=engine, duel=True)
    with TestClient(app) as client:
        assert create(client, board_size=19).status_code == 200
        game = app.state.store.state["games"][0]
        seed_sixty(game)
        engine.second = .40
        game["streak"]["B"] = 2
        header = {"Authorization": "Bearer " + app.state.store.admin_token}
        public = start(client, header, game, wait=False)
        public = wait_status(client, game["id"], "finished")
        assert public["result"]["reason"] == "threshold"
        assert public["result"]["winner"] == game["seats"]["W"]["team"]


def test_duel_referee_and_recovery_preserve_board_and_clock(tmp_path):
    app = create_app(tmp_path, engine=TestEngine(), duel=True)
    with TestClient(app) as client:
        assert create(client).status_code == 200
        game = app.state.store.state["games"][0]
        public = start(client, {"Authorization": "Bearer " + app.state.store.admin_token}, game)
        token = game["seats"][public["turn"]]["token"]
        response = client.post(f"/api/games/{game['id']}/move", headers={"Authorization": "Bearer " + token},
                               json={"version": public["version"], "move": public["candidates"][1]["move"]})
        assert response.status_code == 200
        public = wait_status(client, game["id"])
        board = public["board"]
        credentials = client.get("/api/state").json()["duel"]["credentials"]
    with TestClient(create_app(tmp_path, engine=TestEngine(), duel=True)) as client:
        public = client.get(f"/api/games/{game['id']}").json()
        assert public["status"] == "paused" and public["board"] == board
        assert client.get("/api/state").json()["duel"]["credentials"] == credentials
        assert client.post(f"/api/duel/games/{game['id']}/action", json={"action": "resume"}).status_code == 200
        public = wait_status(client, game["id"])
        assert public["board"] == board and public["remaining"]["B"] <= 600
        result = client.post(f"/api/duel/games/{game['id']}/action",
                             json={"action": "adjudicate", "winner": game["seats"]["B"]["team"]})
        assert result.status_code == 200 and result.json()["status"] == "finished"


def test_duel_rejects_invalid_rules_before_writing_teams(tmp_path):
    app = create_app(tmp_path, engine=TestEngine(), duel=True)
    with TestClient(app) as client:
        for changes in ({"team_a": "  "}, {"team_a": "乙隊"}, {"threshold": .49},
                        {"rules_confirmed": False}, {"board_size": 10}, {"allow_pass": False}):
            assert create(client, **changes).status_code == 400
        assert not app.state.store.state["teams"] and not app.state.store.state["games"]
