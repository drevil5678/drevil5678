import copy
import json

from fastapi.testclient import TestClient

from server.app import create_app, create_event_app
from server.initial_roster import INITIAL_ROSTER
from server.store import Store
from tests.test_app import TestEngine


def test_source_entry_preloads_roster_with_private_persistent_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("TOURNAMENT_DATA", str(tmp_path))
    monkeypatch.setattr("server.app.KataGo", lambda _: TestEngine())
    app = create_event_app()
    assert app.state.initial_roster_loaded
    with TestClient(app) as client:
        auth = {"Authorization": "Bearer " + app.state.store.admin_token}
        state = client.get("/api/admin/state", headers=auth).json()
        teams = state["teams"]
        assert len(teams) == 18
        assert [(t["players"][0], t["players"][1], t["name"]) for t in teams] == list(INITIAL_ROSTER)
        assert [t["order"] for t in teams] == list(range(1, 19))
        assert len({t["play_code"] for t in teams}) == 18
        assert not state["rounds"] and not state["games"] and not state["settings"]["rules_confirmed"]
        assert not any(t["checked_in"] or t["eligibility_checked"] for t in teams)
        public = client.get("/api/state").text
        assert all(t["play_code"] not in public and t["players"][0] not in public for t in teams)
        exported = client.get("/api/admin/export", headers=auth).text
        audit = json.dumps(app.state.store.all_events())
        assert all(t["play_code"] not in exported + audit for t in teams)
        # A real organizer edit survives automatic setup on the next launch.
        payload = {key: teams[1][key] for key in ("name", "players", "mars_points")}
        payload["name"] = "主辦修改後的隊名"
        assert client.put(f"/api/admin/teams/{teams[1]['id']}", headers=auth, json=payload).status_code == 200
    again = create_app(tmp_path, engine=TestEngine(), bootstrap=True)
    assert not again.state.initial_roster_loaded
    assert [t["play_code"] for t in again.state.store.state["teams"]] == [t["play_code"] for t in teams]
    assert again.state.store.state["teams"][1]["name"] == payload["name"]
    assert len([e for e in again.state.store.all_events() if e["kind"] == "initial_roster"]) == 1
    again.state.store.db.close()
    other = Store(tmp_path / "other-host")
    assert other.prepare_initial_roster()
    assert not {t["play_code"] for t in teams} & {t["play_code"] for t in other.state["teams"]}
    other.db.close()


def test_existing_empty_event_gets_roster_but_keeps_rules(tmp_path):
    store = Store(tmp_path)
    store.state["settings"].update(komi=6.5, threshold=.35)
    token = store.admin_token
    store.save()
    assert store.prepare_initial_roster()
    assert store.state["settings"]["komi"] == 6.5 and store.state["settings"]["threshold"] == .35
    assert store.admin_token == token
    store.db.close()


def test_existing_roster_games_and_seed_marker_never_replaced(tmp_path):
    for field, value in (("teams", [{"id": "existing", "name": "既有隊伍", "play_code": "keep-private-code"}]),
                         ("games", [{"id": "old-game", "moves": [["B", "D4"]]}]),
                         ("rounds", [{"id": "old-round"}]), ("initial_roster_version", "already-initialized")):
        store = Store(tmp_path / field)
        store.state[field] = value
        store.save()
        before = copy.deepcopy(store.state)
        assert not store.prepare_initial_roster()
        assert store.state == before
        store.db.close()
