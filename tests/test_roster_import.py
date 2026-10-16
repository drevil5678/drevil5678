import json

from fastapi.testclient import TestClient

from server.app import create_app
from server.credentials import new_team_code
from server.store import Store
from tests.test_app import TestEngine


def test_eighteen_preissued_tsv_teams_keep_codes_order_and_privacy(tmp_path):
    codes = [new_team_code() for _ in range(18)]
    names = ["alphago", "AlphaGo"] + [f"測試隊{i}" for i in range(2, 18)]
    roster = "\ufeff隊長姓名\t隊員姓名\t隊名\t點棋碼\n" + "\n".join(
        f"測試隊長{i}\t{'暫無' if i == 0 else '測試𠄘'}\t{name}\t{code.lower()}"
        for i, (name, code) in enumerate(zip(names, codes)))
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        auth = {"Authorization": "Bearer " + app.state.store.admin_token}
        assert client.post("/api/admin/teams/import", json={"csv": roster}).status_code == 401
        assert client.post("/api/admin/teams/import", headers=auth, json={"csv": roster}).json() == {"imported": 18}
        teams = client.get("/api/admin/state", headers=auth).json()["teams"]
        assert [t["name"] for t in teams] == names
        assert [t["order"] for t in teams] == list(range(1, 19))
        assert [t["play_code"] for t in teams] == codes
        assert not any(t["checked_in"] or t["eligibility_checked"] for t in teams)
        assert client.post("/api/admin/teams/import", headers=auth, json={"csv": roster}).status_code == 409
        assert len(app.state.store.state["teams"]) == 18
        serialized = client.get("/api/state").text + client.get("/api/admin/export", headers=auth).text
        serialized += json.dumps(app.state.store.all_events())
        assert all(code not in serialized for code in codes)
        first = teams[0]
        payload = {key: first[key] for key in ("name", "players", "mars_points")}
        payload.update(checked_in=True, eligibility_checked=True)
        response = client.put(f"/api/admin/teams/{first['id']}", headers=auth, json=payload)
        assert response.status_code == 400 and "補齊" in response.text
        payload["players"][1] = "已補齊的隊員"
        assert client.put(f"/api/admin/teams/{first['id']}", headers=auth, json=payload).json()["play_code"] == codes[0]
    reopened = Store(tmp_path)
    assert [t["play_code"] for t in reopened.state["teams"]] == codes
    reopened.db.close()


def test_import_rejects_duplicate_or_invalid_codes_atomically(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        auth = {"Authorization": "Bearer " + app.state.store.admin_token}
        code = new_team_code()
        header = "name,captain,member,play_code\n"
        first = f"甲隊,甲長,甲員,{code}\n"
        for second, status in ((f"乙隊,乙長,乙員,{code.lower().replace('-', '')}", 409),
                               ("乙隊,乙長,乙員,INVALID", 400), ("乙隊,乙長", 400)):
            response = client.post("/api/admin/teams/import", headers=auth, json={"csv": header + first + second})
            assert response.status_code == status
            assert app.state.store.state["teams"] == []
            assert code not in response.text
        assert client.post("/api/admin/teams/import", headers=auth, json={"csv": header + first}).status_code == 200
        before = json.dumps(app.state.store.state)
        assert client.post("/api/admin/teams/import", headers=auth, json={"csv": header + f"乙隊,乙長,乙員,{code}"}).status_code == 409
        assert json.dumps(app.state.store.state) == before
        # Original CSV without a credential column remains supported.
        assert client.post("/api/admin/teams/import", headers=auth, json={"csv": "name,captain,member\n丙隊,丙長,丙員"}).status_code == 200
        assert app.state.store.state["teams"][1]["play_code"] != code


def test_eighteen_teams_complete_six_swiss_rounds_without_repeat_opponents(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        auth = {"Authorization": "Bearer " + app.state.store.admin_token}
        for i in range(18):
            assert client.post("/api/admin/teams", headers=auth, json={
                "name": f"隊{i+1:02}", "players": [f"甲{i}", f"乙{i}"],
                "checked_in": True, "eligibility_checked": True}).status_code == 200
        assert client.post("/api/admin/settings", headers=auth, json={"rules_confirmed": True}).status_code == 200
        seen = set()
        for number in range(1, 7):
            assert client.post("/api/admin/rounds/swiss", headers=auth).status_code == 200
            games = [g for g in app.state.store.state["games"] if g["round"] == number]
            assert len(games) == 9
            assert client.post("/api/admin/rounds/swiss", headers=auth).status_code == 409
            for index, game in enumerate(games):
                pair = frozenset(s["team"] for s in game["seats"].values())
                assert len(pair) == 2 and pair not in seen
                seen.add(pair)
                color = "B" if (index + number) % 3 else "W"
                response = client.post(f"/api/admin/games/{game['id']}/action", headers=auth, json={
                    "action": "adjudicate", "winner": game["seats"][color]["team"],
                    "points": 1.25 if index % 2 else 1, "reason": "synthetic Swiss verification"})
                assert response.status_code == 200
        assert len(seen) == 54
