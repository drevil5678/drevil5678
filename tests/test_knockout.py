from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from server.app import create_app
from server.initial_roster import INITIAL_CODES, INITIAL_ROSTER
from tests.test_app import TestEngine


def admin(client):
    return {"Authorization": "Bearer " + client.app.state.store.admin_token}


def initialize(client, **extra):
    ids = [team["id"] for team in client.get("/api/state").json()["teams"][:4]]
    return client.post("/api/knockout/setup", headers=admin(client), json={
        "team_ids": ids, "board_size": 9, "visits": 16, "rules_confirmed": True, **extra,
    })


def find(client, ident):
    return next(match for stage in client.get("/api/bracket").json()["stages"] for match in stage["matches"] if match["id"] == ident)


def win(client, ident, winner=None, points=1, **options):
    response = client.post(f"/api/knockout/matches/{ident}/next", headers=admin(client), json=options)
    assert response.status_code == 200, response.text
    game = response.json()
    winner = winner or game["seats"]["B"]["team"]
    response = client.post(f"/api/admin/games/{game['id']}/action", headers=admin(client),
                           json={"action": "adjudicate", "winner": winner, "points": points})
    assert response.status_code == 200
    return game, winner


def test_builtin_roster_and_host_only_four_team_selection(tmp_path):
    app = create_app(tmp_path, engine=TestEngine(), knockout=True)
    with TestClient(app, base_url="http://192.168.50.10:8080", client=("192.168.50.11", 1234)) as client:
        teams = client.get("/api/state").json()["teams"]
        assert [team["name"] for team in teams] == [team[2] for team in INITIAL_ROSTER]
        ids = [team["id"] for team in teams]
        payload = {"team_ids": ids[:4], "rules_confirmed": True}
        assert client.post("/api/knockout/setup", json=payload).status_code == 401
        assert initialize(client, team_ids=[ids[0]] * 4).status_code == 400
        assert initialize(client, team_ids=ids[:3]+["unknown"]).status_code == 400
        for count in (0, 3, 5, 8):
            assert initialize(client, team_ids=ids[:count]).status_code == 422
        assert initialize(client, team_ids=[ids[4], ids[1], ids[10], ids[15]]).status_code == 200
        tree = client.get("/api/bracket").json()
        assert [stage["stage"] for stage in tree["stages"]] == ["semi", "final"]
        assert [[team["id"] for team in match["teams"]] for match in tree["stages"][0]["matches"]] == [[ids[4], ids[1]], [ids[10], ids[15]]]
        assert all(team is None for team in tree["stages"][1]["matches"][0]["teams"])
        assert initialize(client).status_code == 409
        public = client.get("/api/state")
        assert "duel" not in public.json()
        assert all(code not in public.text for code in INITIAL_CODES)
        assert client.post("/api/duel/new", json={}).status_code == 404
        assert client.get("/api/admin/state").status_code == 401


@pytest.mark.parametrize("split_final", [False, True], ids=["final-2-0", "final-2-1"])
def test_semi_bo1_final_bo3_and_single_point25_win(tmp_path, split_final):
    with TestClient(create_app(tmp_path, engine=TestEngine(), knockout=True)) as client:
        assert initialize(client).status_code == 200
        for ident in ("quarter-1", "final-1"):
            assert client.post(f"/api/knockout/matches/{ident}/next", headers=admin(client), json={}).status_code == 409
        _, first = win(client, "semi-1", points=1.25)
        assert find(client, "semi-1")["best_of"] == 1
        assert find(client, "semi-1")["winner"]["id"] == first
        assert client.post("/api/knockout/matches/semi-1/next", headers=admin(client), json={}).status_code == 409
        assert client.post("/api/knockout/matches/final-1/next", headers=admin(client), json={}).status_code == 409
        _, second = win(client, "semi-2")
        assert [team["id"] for team in find(client, "final-1")["teams"]] == [first, second]
        first_final, _ = win(client, "final-1", second, points=1.25, device_a=True)
        assert first_final["remaining"] == {"B": 300, "W": 600}
        assert find(client, "final-1")["wins"][second] == 1
        assert client.get("/api/bracket").json()["champion"] is None
        if split_final:
            win(client, "final-1", first)
            assert client.get("/api/bracket").json()["champion"] is None
        final_game, _ = win(client, "final-1", second)
        assert final_game["remaining"] == {"B": 600, "W": 600}
        assert client.get("/api/bracket").json()["champion"]["id"] == second
        assert client.post("/api/knockout/matches/final-1/next", headers=admin(client), json={}).status_code == 409


def test_both_semifinals_can_run_together_without_duplicate_games(tmp_path):
    with TestClient(create_app(tmp_path, engine=TestEngine(), knockout=True)) as client:
        assert initialize(client).status_code == 200
        assert client.post("/api/knockout/matches/semi-1/next", json={}).status_code == 401
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(lambda index: client.post(f"/api/knockout/matches/semi-{index}/next", headers=admin(client), json={}), [1, 1, 2, 2]))
        assert sorted(response.status_code for response in responses) == [200, 200, 409, 409]
        assert {game["knockout_match"] for game in client.app.state.store.state["games"]} == {"semi-1", "semi-2"}


def test_codes_validate_on_backend_for_remote_players_and_do_not_grant_host_access(tmp_path):
    app = create_app(tmp_path, engine=TestEngine(), knockout=True)
    with TestClient(app, base_url="http://192.168.50.10:8080", client=("192.168.50.12", 4321)) as client:
        assert initialize(client).status_code == 200
        game = client.post("/api/knockout/matches/semi-1/next", headers=admin(client), json={}).json()
        path = f"/api/games/{game['id']}"
        assert client.get(path+"/seat").status_code == 403
        for code in ("WRONG", INITIAL_CODES[4]):
            assert client.get(path+"/seat", headers={"Authorization": "Bearer "+code}).status_code == 403
        credential = {"Authorization": "Bearer "+INITIAL_CODES[0]}
        assert client.get(path+"/seat", headers=credential).json()["team"] == game["seats"]["B"]["team"]
        assert client.post(path+"/device", headers=credential, json={"own_device": True}).json()["remaining"]["B"] == 300
        assert client.post("/api/knockout/matches/semi-2/next", headers=credential, json={}).status_code == 401
        assert client.post(f"/api/admin/games/{game['id']}/action", headers=credential, json={"action":"adjudicate","winner":game["seats"]["B"]["team"]}).status_code == 401
        assert client.post(path+"/device", headers={**credential,"Origin":"http://evil.example"}, json={"own_device":False}).status_code == 403
        assert client.post(f"/api/duel/games/{game['id']}/action", json={"action":"adjudicate","winner":game["seats"]["B"]["team"]}).status_code == 404


def test_series_roster_and_codes_survive_restart(tmp_path):
    with TestClient(create_app(tmp_path, engine=TestEngine(), knockout=True)) as client:
        assert initialize(client).status_code == 200
        win(client, "semi-1"); win(client, "semi-2")
        _, winner = win(client, "final-1")
        before = client.get("/api/bracket").json()
        roster = [(team["id"], team["play_code"]) for team in client.app.state.store.state["teams"]]
    with TestClient(create_app(tmp_path, engine=TestEngine(), knockout=True)) as client:
        assert client.get("/api/bracket").json() == before
        assert [(team["id"],team["play_code"]) for team in client.app.state.store.state["teams"]] == roster
        win(client, "final-1", winner)
        assert client.get("/api/bracket").json()["champion"]["id"] == winner
