import copy
import json

from fastapi.testclient import TestClient

from server.app import create_app, create_event_app
from server.initial_roster import INITIAL_CODES, INITIAL_ROSTER, RULE_VERSION
from server.store import Store
from tests.test_app import TestEngine


def test_source_entry_preloads_roster_with_static_persistent_codes(tmp_path, monkeypatch):
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
        assert all(t["checked_in"] and t["eligibility_checked"] for t in teams)
        assert [t["play_code"] for t in teams] == list(INITIAL_CODES)
        assert teams[0]["players"][1] == "暫無"
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
    assert [t["play_code"] for t in other.state["teams"]] == list(INITIAL_CODES)
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


def test_policy_migration_replaces_old_default_codes_once_without_changing_games(tmp_path):
    store = Store(tmp_path)
    store.prepare_initial_roster()
    teams = store.state["teams"]
    teams[0].update(play_code="previous-local-code", checked_in=False, eligibility_checked=False)
    teams.append({"id": "custom", "name": "主辦新增隊伍", "players": ["甲", "乙"],
                  "checked_in": False, "eligibility_checked": False, "play_code": INITIAL_CODES[0], "order": 19})
    game = {"id": "played", "stage": "swiss", "round": 1, "status": "finished",
            "seats": {"B": {"team": teams[0]["id"], "token": "legacy-game-token"},
                      "W": {"team": teams[1]["id"], "token": "other-legacy-token"}},
            "moves": [["B", "D4"]], "result": {"winner": teams[0]["id"], "points": 1}}
    store.state["games"] = [copy.deepcopy(game)]
    store.state["rounds"] = [{"id": "old-round", "stage": "swiss", "number": 1}]
    store.state.pop("rule_version")
    store.save()
    store.db.close()
    migrated = Store(tmp_path)
    assert migrated.state["rule_version"] == RULE_VERSION
    assert migrated.state["teams"][0]["play_code"] == INITIAL_CODES[0]
    assert migrated.state["teams"][-1]["play_code"] not in INITIAL_CODES
    assert all(t["checked_in"] and t["eligibility_checked"] for t in migrated.state["teams"])
    assert migrated.state["games"] == [game]
    # An organizer may rotate a code later without restarting this migration.
    migrated.state["teams"][0]["play_code"] = "deliberately-rotated"
    migrated.save()
    migrated.db.close()
    reopened = Store(tmp_path)
    assert reopened.state["teams"][0]["play_code"] == "deliberately-rotated"
    assert reopened.state["games"] == [game]
    reopened.db.close()


def test_printable_roster_matches_static_program_defaults():
    import csv
    from pathlib import Path
    with (Path(__file__).resolve().parents[1] / "config" / "team-codes.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [(r["隊長姓名"], r["隊員姓名"], r["隊名"]) for r in rows] == list(INITIAL_ROSTER)
    assert [r["點棋碼"] for r in rows] == list(INITIAL_CODES)
