import json
import re
from fastapi.testclient import TestClient

from server.app import create_app
from server.store import Store
from tests.test_app import TestEngine


def setup(client, app):
    headers = {"Authorization": "Bearer "+app.state.store.admin_token}
    client.post("/api/admin/settings", headers=headers, json={"rules_confirmed": True})
    teams = []
    for i in range(4):
        r = client.post("/api/admin/teams", headers=headers, json={
            "name": f"隊{i}", "players": [f"私密隊長{i}", f"私密隊員{i}"],
            "checked_in": True, "eligibility_checked": True})
        assert r.status_code == 200
        teams.append(r.json())
    return headers, teams


def test_preissued_code_survives_round_changes_and_rejects_other_teams(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        admin, teams = setup(client, app)
        own = teams[0]
        assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}(-[A-HJ-NP-Z2-9]{4}){2}", own["play_code"])
        auth = {"Authorization": "Bearer "+own["play_code"].replace("-", "").lower()}
        assert client.get("/api/team/identify", headers=auth).json() == {"id": own["id"], "name": own["name"]}
        before = client.get(f"/api/teams/{own['id']}/history").json()
        assert not before["upcoming"] and before["next"]["status"] == "waiting_pairing"
        assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
        first = client.get("/api/admin/state", headers=admin).json()["games"]
        mine = next(g for g in first if any(s["team"] == own["id"] for s in g["seats"].values()))
        assert client.get(f"/api/games/{mine['id']}/seat", headers=auth).json()["credential"] == "team"
        other = next(g for g in first if g["id"] != mine["id"])
        assert client.get(f"/api/games/{other['id']}/seat", headers=auth).status_code == 403
        for game in first:
            assert client.post(f"/api/admin/games/{game['id']}/action", headers=admin, json={
                "action": "adjudicate", "winner": game["seats"]["B"]["team"], "points": 1, "reason": "test"}).status_code == 200
        past = client.get(f"/api/teams/{own['id']}/history").json()
        assert len(past["history"]) == 1 and not past["upcoming"]
        assert past["next"]["status"] == "waiting_pairing" and "對手" in past["next"]["message"]
        assert past["next"]["round"] == 2
        assert client.post("/api/admin/rounds/swiss", headers=admin).status_code == 200
        future = client.get(f"/api/teams/{own['id']}/history").json()
        assert future["next"]["status"] == "scheduled" and len(future["upcoming"]) == 1
        assert future["upcoming"][0]["opponent_id"] != past["history"][0]["opponent_id"]
        assert client.get(f"/api/games/{future['upcoming'][0]['id']}/seat", headers=auth).status_code == 200
        assert client.get("/api/admin/state", headers=admin).json()["teams"][0]["play_code"] == own["play_code"]
    reopened = Store(tmp_path)
    assert reopened.state["teams"][0]["play_code"] == own["play_code"]
    reopened.db.close()


def test_code_reset_revokes_stable_and_legacy_credentials(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        admin, teams = setup(client, app)
        client.post("/api/admin/rounds/swiss", headers=admin)
        game = app.state.store.state["games"][0]
        ident = game["seats"]["B"]["team"]
        team = next(t for t in teams if t["id"] == ident)
        old, legacy = team["play_code"], game["seats"]["B"]["token"]
        assert client.post(f"/api/admin/teams/{ident}/code", json={"reason": "lost"}).status_code == 401
        fresh = client.post(f"/api/admin/teams/{ident}/code", headers=admin, json={"reason": "lost"}).json()["play_code"]
        for value in (old, legacy):
            assert client.get(f"/api/games/{game['id']}/seat", headers={"Authorization": "Bearer "+value}).status_code == 403
        assert client.get(f"/api/games/{game['id']}/seat", headers={"Authorization": "Bearer "+fresh}).status_code == 200
        assert fresh not in json.dumps(app.state.store.state["events"])


def test_codes_never_appear_in_public_views_or_export(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        admin, teams = setup(client, app)
        client.post("/api/admin/rounds/swiss", headers=admin)
        game = app.state.store.state["games"][0]
        public = client.get("/api/state").text + client.get(f"/api/games/{game['id']}").text
        public += "".join(client.get(f"/api/teams/{t['id']}/history").text for t in teams)
        exported = client.get("/api/admin/export", headers=admin).text
        assert "play_code" not in exported and "私密隊長" not in public
        for t in teams:
            assert t["play_code"] not in public and t["play_code"] not in exported
        assert client.get("/api/teams/missing/history").status_code == 404


def test_legacy_snapshot_gets_persistent_team_codes_without_changing_games(tmp_path):
    store = Store(tmp_path)
    store.state["teams"] = [{"id": "legacy", "name": "old", "checked_in": True, "order": 1}]
    store.state["games"] = [{"id": "old-game", "seats": {"B": {"team": "legacy", "token": "old-seat"}}, "moves": [["B", "D4"]]}]
    store.save(); store.db.close()
    again = Store(tmp_path)
    code = again.state["teams"][0]["play_code"]
    assert again.state["games"][0]["seats"]["B"]["token"] == "old-seat"
    assert again.state["games"][0]["moves"] == [["B", "D4"]]
    again.db.close()
    third = Store(tmp_path)
    assert third.state["teams"][0]["play_code"] == code
    third.db.close()


def test_participant_guide_has_no_organizer_operations(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        text = client.get("/").text
        guide = text.split('<section id="guide"', 1)[1].split('</section>', 1)[0]
        for private in ("裁判與主辦方", "50 台電腦的配置", "GPU", "資料庫", "管理金鑰"):
            assert private not in guide
        assert "等待配對" in guide and "雙人單機練習" in guide
        assert client.get("/openapi.json").status_code == 404
