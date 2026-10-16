"""Organizer-requested pregame, scoring and independent-table regressions."""
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from tests.test_app import TestEngine, start, wait_status


def authorization(code):
    return {"Authorization": "Bearer " + code}


@contextmanager
def event(tmp_path, count=2, engine=None):
    app = create_app(tmp_path, engine=engine or TestEngine())
    with TestClient(app) as client:
        admin = authorization(app.state.store.admin_token)
        response = client.post("/api/admin/settings", headers=admin, json={
            "board_size": 9, "komi": 7.5, "rules": "chinese", "rules_confirmed": True})
        assert response.status_code == 200
        teams = []
        for number in range(count):
            response = client.post("/api/admin/teams", headers=admin, json={
                "name": f"測試隊{number + 1}", "players": [f"隊長{number}", f"隊員{number}"]})
            assert response.status_code == 200
            teams.append(response.json())
        yield client, app, admin, teams


def post(client, endpoint, code, body=None, expected=200):
    response = client.post(endpoint, headers=authorization(code), json=body)
    assert response.status_code == expected, response.text
    return response.json()


def credentials(game, teams):
    codes = {team["id"]: team["play_code"] for team in teams}
    return {color: codes[seat["team"]] for color, seat in game["seats"].items()}


def fixed_dice(monkeypatch, values):
    """Values are actual die faces, not randbelow's zero-based output."""
    rolls = iter(values)
    monkeypatch.setattr("server.app.secrets.randbelow", lambda upper: next(rolls) - 1)


def roll_and_choose(client, game, codes, color="B"):
    for seat in ("B", "W"):
        public = post(client, f"/api/games/{game['id']}/dice", codes[seat], {"attempt": 1})
    assert public["pregame"]["winner"] == game["seats"]["B"]["team"]
    return post(client, f"/api/games/{game['id']}/color", codes["B"], {"attempt": 1, "color": color})


def test_dice_ties_are_public_and_only_winner_can_choose_before_both_ready(tmp_path, monkeypatch):
    with event(tmp_path) as (client, app, admin, teams):
        assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
        game = app.state.store.state["games"][0]
        codes = credentials(game, teams)
        endpoint = f"/api/games/{game['id']}"
        # Nobody may guess/start the clock before the organizer opens the round.
        post(client, endpoint + "/dice", codes["B"], {"attempt": 1}, expected=409)
        response = client.post("/api/admin/rounds/current/start", headers=admin)
        assert response.json() == {"armed": 1, "started": 0}
        assert game["status"] == "ready" and game["clock_started"] is None
        post(client, endpoint + "/ready", codes["B"], {"attempt": 1, "ready": True}, expected=409)
        fixed_dice(monkeypatch, [1, 6, 3, 4, 6, 6, 1, 1])
        first = post(client, endpoint + "/dice", codes["B"], {"attempt": 1})
        assert first["pregame"]["rolls"] == {game["seats"]["B"]["team"]: [1, 6]}
        post(client, endpoint + "/dice", codes["B"], {"attempt": 1}, expected=409)
        tied = post(client, endpoint + "/dice", codes["W"], {"attempt": 1})
        assert tied["pregame"]["attempt"] == 2 and tied["pregame"]["rolls"] == {}
        history = tied["pregame"]["history"]
        assert history == [{"attempt": 1, "rolls": {
            game["seats"]["B"]["team"]: [1, 6], game["seats"]["W"]["team"]: [3, 4]}, "tie": True}]
        # A late request from the previous animation cannot roll into the new attempt.
        post(client, endpoint + "/dice", codes["W"], {"attempt": 1}, expected=409)
        post(client, endpoint + "/dice", codes["B"], {"attempt": 2})
        result = post(client, endpoint + "/dice", codes["W"], {"attempt": 2})
        assert result["pregame"]["winner"] == game["seats"]["B"]["team"]
        assert result["pregame"]["history"][-1]["tie"] is False
        post(client, endpoint + "/color", codes["W"], {"attempt": 2, "color": "B"}, expected=403)
        post(client, endpoint + "/color", codes["B"], {"attempt": 2, "color": "B"})
        one = post(client, endpoint + "/ready", codes["B"], {"attempt": 2, "ready": True})
        assert one["status"] == "ready" and game["clock_started"] is None
        post(client, endpoint + "/color", codes["B"], {"attempt": 2, "color": "W"}, expected=409)
        assert game["remaining"] == {"B": 600, "W": 600}
        post(client, endpoint + "/ready", codes["W"], {"attempt": 2, "ready": True})
        public = wait_status(client, game["id"])
        assert 590 < public["remaining"]["B"] <= 600 and public["remaining"]["W"] == 600
        assert game["clock_started"] is not None
        post(client, endpoint + "/ready", codes["B"], {"attempt": 2, "ready": False}, expected=409)


def test_bulk_start_arms_two_tables_without_skipping_either_teams_ready(tmp_path, monkeypatch):
    with event(tmp_path, count=4) as (client, app, admin, teams):
        assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
        games = app.state.store.state["games"]
        assert client.post("/api/admin/rounds/current/start").status_code == 401
        assert client.post("/api/admin/rounds/current/start", headers=admin).json() == {"armed": 2, "started": 0}
        assert all(game["status"] == "ready" and game["clock_started"] is None for game in games)
        fixed_dice(monkeypatch, [6, 6, 1, 1] * 2)
        for index, game in enumerate(games):
            codes = credentials(game, teams)
            roll_and_choose(client, game, codes)
            post(client, f"/api/games/{game['id']}/ready", codes["B"], {"attempt": 1, "ready": True})
            assert game["status"] == "ready" and game["clock_started"] is None
            post(client, f"/api/games/{game['id']}/ready", codes["W"], {"attempt": 1, "ready": True})
            wait_status(client, game["id"])
            if index == 0:
                assert games[1]["status"] == "ready" and games[1]["clock_started"] is None
        assert all(game["status"] == "playing" for game in games)
        assert client.post("/api/admin/rounds/current/start", headers=admin).json() == {"armed": 0, "started": 0}


def adjudicate_current(client, app, admin):
    current = app.state.store.state["rounds"][-1]["id"]
    for game in app.state.store.state["games"]:
        if game["round_id"] == current:
            response = client.post(f"/api/admin/games/{game['id']}/action", headers=admin,
                json={"action": "adjudicate", "winner": game["seats"]["B"]["team"], "points": 1})
            assert response.status_code == 200  # No adjudication reason is required.


def test_final_fifteen_ten_minutes_and_device_follows_team_after_color_swap(tmp_path, monkeypatch):
    with event(tmp_path, count=8) as (client, app, admin, teams):
        # Reach a real final through the public organizer APIs, without fabricated games.
        for _ in range(5):
            assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
            adjudicate_current(client, app, admin)
        for _ in range(2):
            assert client.post("/api/admin/rounds/playoff", headers=admin).status_code == 200
            adjudicate_current(client, app, admin)
        assert client.post("/api/admin/rounds/playoff", headers=admin).status_code == 200
        game = app.state.store.state["games"][-1]
        assert game["stage"] == "final" and game["remaining"] == {"B": 900, "W": 900}
        old_black = game["seats"]["B"]["team"]
        codes = credentials(game, teams)
        post(client, f"/api/games/{game['id']}/device", codes["B"], {"own_device": True})
        assert game["remaining"] == {"B": 600, "W": 900}
        assert client.post("/api/admin/rounds/current/start", headers=admin).json() == {"armed": 1, "started": 0}
        fixed_dice(monkeypatch, [6, 6, 1, 1])
        swapped = roll_and_choose(client, game, codes, color="W")
        assert swapped["seats"]["W"]["team"] == old_black
        assert swapped["seats"]["W"]["own_device"] and not swapped["seats"]["B"]["own_device"]
        assert swapped["remaining"] == {"B": 900, "W": 600}
        assert client.get(f"/api/games/{game['id']}/seat", headers=authorization(codes["B"])).json()["color"] == "W"
        post(client, f"/api/games/{game['id']}/ready", codes["B"], {"attempt": 1, "ready": True})
        post(client, f"/api/games/{game['id']}/device", codes["B"], {"own_device": False}, expected=409)
        post(client, f"/api/games/{game['id']}/ready", codes["W"], {"attempt": 1, "ready": True})
        public = wait_status(client, game["id"])
        assert 890 < public["remaining"]["B"] <= 900 and public["remaining"]["W"] == 600


class PassFirstEngine(TestEngine):
    async def analyze(self, moves, settings):
        response = await super().analyze(moves, settings)
        legal = response["moveInfos"][0]
        legal["winrate"] = .1
        return {"moveInfos": [{"move": "pass", "visits": 30, "winrate": .9}, legal]}


class OnlyPassEngine(TestEngine):
    async def analyze(self, moves, settings):
        return {"moveInfos": [{"move": "pass", "visits": 30, "winrate": .9}]}


@pytest.mark.parametrize("engine_class", [PassFirstEngine, OnlyPassEngine])
def test_double_pass_scores_current_board_with_komi_for_one_point(tmp_path, engine_class):
    with event(tmp_path, engine=engine_class()) as (client, app, admin, teams):
        assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
        game = app.state.store.state["games"][0]
        # Make the boundary observable immediately: a legal .9 pass must protect
        # the player even when the only board alternative is below .4.
        app.state.store.state["settings"].update(loss_start_ply=1, loss_turns=1)
        public = start(client, admin, game)
        assert public["forbidden"] is None and public["best_legal_winrate"] == .9
        assert public["streak"] == {"B": 0, "W": 0}
        assert public["move_number"] == 1 and public["moves_played"] == 0
        for color in ("B", "W"):
            public = post(client, f"/api/games/{game['id']}/move", game["seats"][color]["token"],
                          {"move": "pass", "version": public["version"]})
            public = wait_status(client, game["id"], "playing" if color == "B" else "finished")
        assert public["result"] == {"winner": game["seats"]["W"]["team"], "reason": "score", "points": 1}
        assert public["score"]["black"] == 0 and public["score"]["white"] == 7.5
        assert public["score"]["komi"] == 7.5 and public["score"]["margin"] == 7.5
        assert public["score"]["position"] == "current-board" and not public["score"]["dead_stones_removed"]
        assert public["move_number"] == 3 and public["moves_played"] == 2
        scores = {row["id"]: row["score"] for row in client.get("/api/state").json()["standings"]}
        assert scores[game["seats"]["W"]["team"]] == 1 and scores[game["seats"]["B"]["team"]] == 0


def test_special_tables_do_not_change_rankings_or_block_next_official_round(tmp_path):
    with event(tmp_path, count=4) as (client, app, admin, teams):
        assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
        swiss = list(app.state.store.state["games"])
        before = client.get("/api/state").json()["standings"]
        body = {"team_a": teams[0]["id"], "team_b": teams[2]["id"]}
        assert client.post("/api/admin/special-tables", json=body).status_code == 401
        assert client.post("/api/admin/special-tables", headers=admin,
                           json={"team_a": teams[0]["id"], "team_b": teams[0]["id"]}).status_code == 400
        special = client.post("/api/admin/special-tables", headers=admin, json=body).json()["game"]
        assert special["stage"] == "special"
        response = client.post(f"/api/admin/games/{special['id']}/action", headers=admin,
            json={"action": "adjudicate", "winner": teams[0]["id"], "points": 1.25})
        assert response.status_code == 200
        assert client.get("/api/state").json()["standings"] == before
        # A second unstarted special table must not hold up official pairings.
        active = client.post("/api/admin/special-tables", headers=admin, json=body).json()["game"]
        for game in swiss:
            response = client.post(f"/api/admin/games/{game['id']}/action", headers=admin,
                json={"action": "adjudicate", "winner": game["seats"]["B"]["team"], "points": 1})
            assert response.status_code == 200
        assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
        assert client.get(f"/api/games/{active['id']}").json()["status"] == "ready"
        standings = client.get("/api/state").json()["standings"]
        assert sum(row["score"] for row in standings) == 2
        history = client.get(f"/api/teams/{teams[0]['id']}/history").json()
        assert any(game["id"] == special["id"] and game["stage"] == "special" for game in history["history"])


def test_special_tables_require_confirmed_rules_and_available_ai_without_creating_games(tmp_path):
    engine = TestEngine()
    with event(tmp_path, engine=engine) as (client, app, admin, teams):
        body = {"team_a": teams[0]["id"], "team_b": teams[1]["id"]}
        assert client.post("/api/admin/settings", headers=admin,
                           json={"rules_confirmed": False}).status_code == 200
        assert client.post("/api/admin/special-tables", headers=admin, json=body).status_code == 409
        assert not app.state.store.state["rounds"] and not app.state.store.state["games"]
        assert client.post("/api/admin/settings", headers=admin,
                           json={"rules_confirmed": True}).status_code == 200
        engine.failure = "controlled engine startup failure"
        assert client.post("/api/admin/special-tables", headers=admin, json=body).status_code == 503
        assert not app.state.store.state["rounds"] and not app.state.store.state["games"]


def test_exact_tie_after_double_pass_requires_adjudication_and_cannot_resume(tmp_path):
    with event(tmp_path, engine=OnlyPassEngine()) as (client, app, admin, teams):
        settings = {**app.state.store.state["settings"], "komi": 0}
        assert client.post("/api/admin/settings", headers=admin, json=settings).status_code == 200
        assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
        game = app.state.store.state["games"][0]
        public = start(client, admin, game)
        for color in ("B", "W"):
            public = post(client, f"/api/games/{game['id']}/move", game["seats"][color]["token"],
                          {"move": "pass", "version": public["version"]})
            public = wait_status(client, game["id"], "playing" if color == "B" else "paused")
        assert public["result"] is None
        assert public["score"]["winner"] is None and public["score"]["margin"] == 0
        assert public["score"]["black"] == public["score"]["white"] == 0
        assert game["clock_started"] is None
        assert all(row["score"] == 0 for row in client.get("/api/state").json()["standings"])
        response = client.post(f"/api/admin/games/{game['id']}/action", headers=admin,
                               json={"action": "resume"})
        assert response.status_code == 409
        assert client.get(f"/api/games/{game['id']}").json()["status"] == "paused"
        response = client.post(f"/api/admin/games/{game['id']}/action", headers=admin,
            json={"action": "adjudicate", "winner": game["seats"]["B"]["team"], "points": 1})
        assert response.status_code == 200  # Referee reason remains optional.
        finished = client.get(f"/api/games/{game['id']}").json()
        assert finished["status"] == "finished"
        assert finished["result"]["winner"] == game["seats"]["B"]["team"]
        assert finished["result"]["points"] == 1
        scores = {row["id"]: row["score"] for row in client.get("/api/state").json()["standings"]}
        assert scores[game["seats"]["B"]["team"]] == 1
        assert scores[game["seats"]["W"]["team"]] == 0
