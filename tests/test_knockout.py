from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from server.app import create_app
from tests.test_app import TestEngine


def initialize(client, **extra):
    return client.post("/api/knockout/setup", json={
        "names": [f"隊伍{index}" for index in range(1, 9)], "board_size": 9,
        "visits": 16, "rules_confirmed": True, **extra,
    })


def find(client, ident):
    tree = client.get("/api/bracket").json()
    return next(match for stage in tree["stages"] for match in stage["matches"] if match["id"] == ident)


def win(client, ident, winner=None, points=1):
    response = client.post(f"/api/knockout/matches/{ident}/next", json={})
    assert response.status_code == 200, response.text
    game = response.json()
    winner = winner or game["seats"]["B"]["team"]
    response = client.post(f"/api/duel/games/{game['id']}/action",
                           json={"action": "adjudicate", "winner": winner, "points": points})
    assert response.status_code == 200
    return game, winner


def test_manual_names_pair_order_and_bad_setup_cannot_overwrite(tmp_path):
    app = create_app(tmp_path, engine=TestEngine(), duel=True, knockout=True, bootstrap=True)
    with TestClient(app) as client:
        assert not client.get("/api/state").json()["teams"]
        for names in (["同名"] * 8, ["一隊"] * 7 + ["  "], ["甲"] * 7 + ["長" * 61]):
            assert initialize(client, names=names).status_code == 400
        assert not app.state.store.state["teams"]
        assert initialize(client).status_code == 200
        tree = client.get("/api/bracket").json()
        assert [[team["name"] for team in match["teams"]] for match in tree["stages"][0]["matches"]] == [
            ["隊伍1", "隊伍2"], ["隊伍3", "隊伍4"], ["隊伍5", "隊伍6"], ["隊伍7", "隊伍8"]]
        assert all(team is None for stage in tree["stages"][1:] for match in stage["matches"] for team in match["teams"])
        assert initialize(client).status_code == 409
        assert client.post("/api/duel/new", json={"rules_confirmed": True}).status_code == 409
        assert len(app.state.store.state["teams"]) == 8


def test_semi_and_final_bo3_handle_2_0_2_1_and_single_point25_win(tmp_path):
    app = create_app(tmp_path, engine=TestEngine(), duel=True, knockout=True)
    with TestClient(app) as client:
        assert initialize(client).status_code == 200
        for ident in ("semi-1", "final-1"):
            assert client.post(f"/api/knockout/matches/{ident}/next", json={}).status_code == 409
        quarters = [win(client, f"quarter-{index}")[1] for index in range(1, 5)]
        assert find(client, "quarter-1")["winner"]["id"] == quarters[0]
        assert client.post("/api/knockout/matches/quarter-1/next", json={}).status_code == 409
        _, first = win(client, "semi-1", points=1.25)
        semi = find(client, "semi-1")
        assert semi["wins"][first] == 1 and semi["winner"] is None
        assert find(client, "final-1")["teams"][0] is None
        win(client, "semi-1", first)
        assert find(client, "semi-1")["winner"]["id"] == first
        assert client.post("/api/knockout/matches/semi-1/next", json={}).status_code == 409
        semi2 = find(client, "semi-2")
        a, b = [team["id"] for team in semi2["teams"]]
        win(client, "semi-2", a)
        win(client, "semi-2", b)
        assert find(client, "semi-2")["winner"] is None
        win(client, "semi-2", b)
        assert find(client, "semi-2")["wins"] == {a: 1, b: 2}
        assert [team["id"] for team in find(client, "final-1")["teams"]] == [first, b]
        for winner in (first, b):
            win(client, "final-1", winner)
        assert client.get("/api/bracket").json()["champion"] is None
        final_game, _ = win(client, "final-1", b)
        assert final_game["remaining"] == {"B": 900, "W": 900}
        assert client.get("/api/bracket").json()["champion"]["id"] == b
        assert client.post("/api/knockout/matches/final-1/next", json={}).status_code == 409
        exported = client.get("/api/duel/export")
        assert "knockout-records.json" in exported.headers["content-disposition"]
        assert exported.json()["awards"] == []


def test_only_one_active_board_even_for_concurrent_match_requests(tmp_path):
    app = create_app(tmp_path, engine=TestEngine(), duel=True, knockout=True)
    with TestClient(app) as client:
        assert initialize(client).status_code == 200
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(lambda index: client.post(f"/api/knockout/matches/quarter-{index}/next", json={}), range(1, 5)))
        assert sorted(response.status_code for response in responses) == [200, 409, 409, 409]
        assert len(app.state.store.state["games"]) == 1


def test_series_and_names_survive_restart_without_new_roster(tmp_path):
    app = create_app(tmp_path, engine=TestEngine(), duel=True, knockout=True)
    with TestClient(app) as client:
        assert initialize(client).status_code == 200
        for index in range(1, 5):
            win(client, f"quarter-{index}")
        _, winner = win(client, "semi-1")
        before = client.get("/api/bracket").json()
    with TestClient(create_app(tmp_path, engine=TestEngine(), duel=True, knockout=True, bootstrap=True)) as client:
        assert client.get("/api/bracket").json() == before
        assert len(client.get("/api/state").json()["teams"]) == 8
        win(client, "semi-1", winner)
        assert find(client, "semi-1")["winner"]["id"] == winner


def test_knockout_routes_are_unavailable_in_regular_app_and_remote_peers(tmp_path):
    with TestClient(create_app(tmp_path / "normal", engine=TestEngine())) as client:
        assert initialize(client).status_code == 404
    with TestClient(create_app(tmp_path / "local", engine=TestEngine(), duel=True, knockout=True),
                    client=("192.168.1.42", 1234)) as client:
        assert initialize(client).status_code == client.get("/api/bracket").status_code == 403
