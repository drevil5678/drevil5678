import asyncio
import time
import pytest
from fastapi.testclient import TestClient
from server.app import create_app
from server.go import encode, play


class TestEngine:
    """Explicit test double. Production always invokes a real KataGo process."""
    __test__ = False
    failure = ""
    second = .55
    delay = 0

    async def start(self):
        pass

    async def close(self):
        pass

    async def analyze(self, moves, settings):
        await asyncio.sleep(self.delay)
        n = settings["board_size"]
        board = "."*(n*n)
        history = [board]
        for color, move in moves:
            board, _ = play(board, color, move, n, history)
            history.append(board)
        color = "B" if len(moves)%2 == 0 else "W"
        legal = []
        for i, p in enumerate(board):
            if p != ".":
                continue
            move = encode(i, n)
            try:
                play(board, color, move, n, history)
                legal.append(move)
            except ValueError:
                pass
            if len(legal) == 5:
                break
        return {"moveInfos": [{"move": m, "visits": 20, "winrate": .9 if i == 0 else self.second-(i-1)*.001} for i, m in enumerate(legal)]}


@pytest.fixture
def prepared(tmp_path):
    engine = TestEngine()
    app = create_app(tmp_path, engine=engine)
    with TestClient(app) as client:
        token = app.state.store.admin_token
        header = {"Authorization": "Bearer "+token}
        settings = {**app.state.store.state["settings"], "rules_confirmed": True}
        assert client.post("/api/admin/settings", headers=header, json=settings).status_code == 200
        for i in range(2):
            assert client.post("/api/admin/teams", headers=header, json={"name": f"隊{i}", "players": [f"甲{i}",f"乙{i}"], "checked_in": True, "eligibility_checked": True}).status_code == 200
        assert client.post("/api/admin/rounds/swiss", headers=header).status_code == 200
        g = app.state.store.state["games"][0]
        yield client, app, engine, header, g


def wait_status(client, gid, expected="playing"):
    deadline = time.time()+3
    while time.time() < deadline:
        g = client.get(f"/api/games/{gid}").json()
        if g["status"] == expected:
            return g
        time.sleep(.01)
    raise AssertionError(f"Expected {expected}, got {g}")


def start(client, header, g, *, wait=True, before_ready=None):
    """Arm a game, complete both dice rolls and ready checks, preserve fixture colors."""
    black_team = g["seats"]["B"]["team"]
    credentials = {seat["team"]: seat["token"] for seat in g["seats"].values()}
    assert client.post(f"/api/admin/games/{g['id']}/action", headers=header,
                       json={"action": "start"}).status_code == 200
    for _ in range(50):
        public = client.get(f"/api/games/{g['id']}").json()
        pregame = public["pregame"]
        attempt = pregame["attempt"]
        for team, code in credentials.items():
            if team not in pregame["rolls"]:
                response = client.post(f"/api/games/{g['id']}/dice",
                    headers={"Authorization": "Bearer " + code}, json={"attempt": attempt})
                assert response.status_code == 200, response.text
        public = client.get(f"/api/games/{g['id']}").json()
        if public["pregame"]["winner"]:
            break
    else:
        raise AssertionError("Dice did not produce a non-tied result")
    pregame = public["pregame"]
    response = client.post(f"/api/games/{g['id']}/color",
        headers={"Authorization": "Bearer " + credentials[pregame["winner"]]},
        json={"color": "B" if pregame["winner"] == black_team else "W", "attempt": pregame["attempt"]})
    assert response.status_code == 200, response.text
    if before_ready:
        before_ready()
    for code in credentials.values():
        response = client.post(f"/api/games/{g['id']}/ready",
            headers={"Authorization": "Bearer " + code},
            json={"ready": True, "attempt": pregame["attempt"]})
        assert response.status_code == 200, response.text
    return wait_status(client, g["id"]) if wait else client.get(f"/api/games/{g['id']}").json()


def submit_second(client, g, public):
    h = {"Authorization": "Bearer "+g["seats"][public["turn"]]["token"]}
    return client.post(f"/api/games/{g['id']}/move", headers=h,
                       json={"move":public["candidates"][1]["move"], "version":public["version"]})


def seed_sixty(g):
    n = 19
    for step in range(60):
        color = "B" if step%2 == 0 else "W"
        for i in range(n*n):
            m = encode(i, n)
            try:
                board, _ = play(g["board"], color, m, n, g["history"])
                break
            except ValueError:
                continue
        g["board"] = board
        g["history"].append(board)
        g["moves"].append([color, m])


def test_authority_device_forbidden_and_stale_move(prepared):
    client, app, engine, header, g = prepared
    assert client.post("/api/admin/backup").status_code == 401
    assert "token" not in str(client.get("/api/state").json())
    bh = {"Authorization": "Bearer "+g["seats"]["B"]["token"]}
    assert client.post(f"/api/games/{g['id']}/device", headers=bh, json={"own_device":True}).status_code == 200
    assert g["remaining"]["B"] == 300
    public = start(client, header, g)
    assert client.post(f"/api/games/{g['id']}/move", headers=bh,json={"move":public["forbidden"],"version":public["version"]}).status_code == 400
    disguised = public["forbidden"][0] + "0" + public["forbidden"][1:]
    assert client.post(f"/api/games/{g['id']}/move", headers=bh,json={"move":disguised,"version":public["version"]}).status_code == 400
    assert client.post(f"/api/games/{g['id']}/move", headers=bh,json={"move":public["candidates"][1]["move"],"version":-1}).status_code == 409
    assert client.post(f"/api/games/{g['id']}/move", json={"move":"D4","version":public["version"]}).status_code == 403
    assert submit_second(client,g,public).status_code == 200
    public = wait_status(client,g["id"])
    assert public["turn"] == "W" and len(public["moves"]) == 1
    assert client.post(f"/api/games/{g['id']}/device",headers=bh,json={"own_device":False}).status_code == 409


def test_threshold_40_inclusive_on_own_turns_from_61(prepared):
    client, app, engine, header, g = prepared
    seed_sixty(g)
    engine.second = .4
    public = start(client,header,g)
    assert public["streak"] == {"B":1,"W":0}
    for i in range(4):
        assert submit_second(client,g,public).status_code == 200
        public = wait_status(client,g["id"], "finished" if i == 3 else "playing")
    assert public["streak"] == {"B":3,"W":2}
    assert public["result"] == {"winner":g["seats"]["W"]["team"],"reason":"threshold","points":1.25}
    assert len(public["moves"]) == 64  # Adjudicated at the start of ply 65.


def test_above_threshold_resets_and_resume_does_not_count_twice(prepared):
    client, app, engine, header, g = prepared
    seed_sixty(g)
    engine.second = .4
    public = start(client,header,g)
    assert client.post(f"/api/admin/games/{g['id']}/action",headers=header,json={"action":"pause","reason":"網路異常"}).status_code == 200
    assert client.post(f"/api/admin/games/{g['id']}/action",headers=header,json={"action":"resume"}).status_code == 200
    public = wait_status(client,g["id"])
    assert public["streak"]["B"] == 1
    assert submit_second(client,g,public).status_code == 200
    public = wait_status(client,g["id"])
    engine.second = .400001
    assert submit_second(client,g,public).status_code == 200
    public = wait_status(client,g["id"])
    assert public["streak"]["B"] == 0


def test_timeout_scoring_and_system_pause(prepared):
    client, app, engine, header, g = prepared
    engine.delay = .1
    start(client, header, g, wait=False)
    g["remaining"]["B"] = .03
    time.sleep(.05)
    public = client.get(f"/api/games/{g['id']}").json()
    assert public["status"] == "analyzing" and public["remaining"]["B"] == .03
    public = wait_status(client,g["id"],"finished")
    assert public["result"]["reason"] == "timeout" and public["result"]["points"] == 1


def test_referee_pause_race_archival_and_backup(prepared):
    client, app, engine, header, g = prepared
    engine.delay = .1
    start(client, header, g, wait=False)
    client.post(f"/api/admin/games/{g['id']}/action",headers=header,json={"action":"pause","reason":"測試暫停"})
    time.sleep(.2)
    assert client.get(f"/api/games/{g['id']}").json()["status"] == "paused"
    assert client.post(f"/api/admin/games/{g['id']}/action",headers=header,json={"action":"rematch","reason":"網路中斷"}).status_code == 200
    assert g["status"] == "ready" and g["moves"] == []
    assert any(e["kind"]=="rematch_archive" for e in app.state.store.state["events"])
    assert client.post("/api/admin/backup",headers=header).status_code == 200
    export = client.get("/api/admin/export",headers=header)
    assert export.status_code == 200 and g["seats"]["B"]["token"] not in export.text


def test_restart_recovers_paused_without_charging_downtime(tmp_path):
    app = create_app(tmp_path, engine=TestEngine())
    # Reuse a complete record from a controlled fixture through a separate client.
    with TestClient(app) as client:
        state = app.state.store.state
        state["teams"] = [{"id":"a","name":"甲隊"},{"id":"b","name":"乙隊"}]
        state["games"] = [{"id":"test", "stage":"swiss",
            "seats":{"B":{"team":"a", "token":"old-black", "own_device":False},
                     "W":{"team":"b", "token":"old-white", "own_device":False}},
            "status":"playing","clock_started":time.time()-100,
            "generation":1,"version":1,"remaining":{"B":120,"W":300},"turn":"B"}]
        # A restart test persists state directly; prevent normal shutdown settling.
        state["games"][0]["status"] = "paused"
        app.state.store.save()
    import json
    import sqlite3
    db = sqlite3.connect(tmp_path/"tournament.sqlite3")
    s = json.loads(db.execute("SELECT data FROM state").fetchone()[0])
    s["games"][0]["status"] = "playing"
    db.execute("UPDATE state SET data=?", (json.dumps(s),))
    db.commit(); db.close()
    second = create_app(tmp_path, engine=TestEngine())
    with TestClient(second):
        g = second.state.store.state["games"][0]
        assert g["status"] == "paused" and g["remaining"]["B"] == 120
        assert g["clock_started"] is None


def test_auto_backup_contains_registered_teams(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_BACKUP_SECONDS", "1")
    app = create_app(tmp_path, engine=TestEngine())
    with TestClient(app) as client:
        header = {"Authorization": "Bearer "+app.state.store.admin_token}
        response=client.post("/api/admin/teams",headers=header,json={"name":"自動備份隊","players":["甲","乙"]})
        assert response.status_code == 200
        deadline=time.time()+4
        while time.time()<deadline:
            events=client.get("/api/admin/state",headers=header).json()["events"]
            item=next((e for e in events if e["kind"]=="auto_backup"),None)
            if item:
                break
            time.sleep(.1)
        else:
            raise AssertionError("auto backup did not run")
        import sqlite3
        import json
        backup=sqlite3.connect(tmp_path/"backups"/item["detail"]["filename"])
        saved=json.loads(backup.execute("SELECT data FROM state").fetchone()[0])
        assert saved["teams"][0]["name"] == "自動備份隊"
        backup.close()
