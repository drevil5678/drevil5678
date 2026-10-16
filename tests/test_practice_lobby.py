import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from server.practice_lobby import create_practice_lobby, IDLE_SECONDS
from tests.test_app import TestEngine


def auth(created):
    return {"Authorization": "Bearer " + created["token"]}


def endpoint(created, action=""):
    return "/api/practice-lan/rooms/" + created["game"]["id"] + ("/" + action if action else "")


def ready(client, created, headers=None, moves=None):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = client.get(endpoint(created), headers=headers or auth(created))
        assert response.status_code == 200
        game = response.json()["game"]
        if game["status"] in ("playing", "finished", "error") and (moves is None or len(game["moves"]) == moves):
            return game
        time.sleep(.005)
    raise AssertionError("room analysis did not complete")


def create(client, **settings):
    response = client.post("/api/practice-lan/rooms", json={"board_size": 9, **settings})
    assert response.status_code == 200
    return response.json()


def test_atomic_fifteen_table_limit_and_owner_only_slot_release(tmp_path):
    with TestClient(create_practice_lobby(tmp_path, engine=TestEngine())) as client:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: client.post("/api/practice-lan/rooms", json={}), range(24)))
        assert [r.status_code for r in results].count(200) == 15
        assert [r.status_code for r in results].count(429) == 9
        rooms = [r.json() for r in results if r.status_code == 200]
        listing = client.get("/api/practice-lan/rooms").json()
        assert listing["tables"] == listing["max_tables"] == 15
        first, other = rooms[:2]
        assert client.request("DELETE", endpoint(first), json={"version": 0}, headers=auth(other)).status_code == 403
        assert client.post(endpoint(first, "finish"), headers=auth(first), json={"version": 0}).status_code == 200
        assert client.post("/api/practice-lan/rooms", json={}).status_code == 429  # finished still occupies a table
        current = client.get(endpoint(first)).json()["game"]
        assert client.request("DELETE", endpoint(first), headers=auth(first), json={"version": current["version"]}).status_code == 200
        assert client.get(endpoint(first)).status_code == 404
        assert create(client)["game"]["id"] not in [r["game"]["id"] for r in rooms]
        assert client.get("/api/practice-lan/rooms").json()["tables"] == 15
        public = client.get("/api/practice-lan/rooms").text + client.get(endpoint(other)).text
        assert all(r["token"] not in public for r in rooms)
        assert "owner_token" not in public and "history" not in public


def test_pvp_join_turn_auth_stale_state_and_consensual_undo(tmp_path):
    with TestClient(create_practice_lobby(tmp_path, engine=TestEngine())) as client:
        owner = create(client, player_name="黑方")
        assert owner["game"]["status"] == "waiting" and owner["game"]["viewer"]["color"] == "B"
        with ThreadPoolExecutor(max_workers=2) as executor:
            joined = list(executor.map(lambda n: client.post(endpoint(owner, "join"), json={"player_name": n}), ("白方甲", "白方乙")))
        assert sorted(r.status_code for r in joined) == [200, 409]
        guest = next(r.json() for r in joined if r.status_code == 200)
        game = ready(client, owner)
        assert guest["game"]["viewer"]["color"] == "W"
        assert client.post(endpoint(owner, "move"), headers=auth(guest), json={"version": game["version"], "move": "D4"}).status_code == 403
        assert client.post(endpoint(owner, "move"), json={"version": game["version"], "move": "D4"}).status_code == 403
        assert client.post(endpoint(owner, "move"), headers=auth(owner), json={"version": game["version"], "move": game["forbidden"].lower()}).status_code == 400
        old = game["version"]
        assert client.post(endpoint(owner, "move"), headers=auth(owner), json={"version": old, "move": game["candidates"][1]["move"]}).status_code == 200
        game = ready(client, owner, moves=1)
        assert client.post(endpoint(owner, "move"), headers=auth(owner), json={"version": old, "move": "D4"}).status_code == 409
        request = client.post(endpoint(owner, "undo"), headers=auth(owner), json={"version": game["version"]}).json()["game"]
        assert request["undo_pending"]["color"] == "B" and len(request["moves"]) == 1
        assert client.post(endpoint(owner, "undo-response"), headers=auth(owner), json={"version": request["version"], "accept": True}).status_code == 403
        assert client.post(endpoint(owner, "move"), headers=auth(guest), json={"version": request["version"], "move": "D4"}).status_code == 409
        assert client.post(endpoint(owner, "undo-response"), headers=auth(guest), json={"version": request["version"], "accept": True}).status_code == 200
        game = ready(client, owner, moves=0)
        assert game["turn"] == "B" and game["undo_pending"] is None
        ended = client.post(endpoint(owner, "finish"), headers=auth(guest), json={"version": game["version"]}).json()["game"]
        assert ended["result"] == {"reason": "resign", "winner": "B"}
        assert "KM[7.5]" in client.get(endpoint(owner, "sgf")).text


def test_computer_plays_both_colors_and_undo_returns_human_turn(tmp_path):
    with TestClient(create_practice_lobby(tmp_path, engine=TestEngine())) as client:
        for color in ("B", "W"):
            room = create(client, mode="ai", human_color=color)
            game = ready(client, room, moves=0 if color == "B" else 1)
            assert game["turn"] == color and game["viewer"]["color"] == color
            if color == "W":
                assert game["moves"][0] == ["B", "B9"]  # computer respects the ban on A9
            move = game["candidates"][1]["move"]
            assert client.post(endpoint(room, "move"), headers=auth(room), json={"version": game["version"], "move": move}).status_code == 200
            game = ready(client, room, moves=2 if color == "B" else 3)
            assert game["turn"] == color
            assert client.post(endpoint(room, "undo"), headers=auth(room), json={"version": game["version"]}).status_code == 200
            game = ready(client, room, moves=0 if color == "B" else 1)
            assert game["turn"] == color


def test_independent_rooms_background_ai_and_closed_task_cannot_restore_room(tmp_path):
    class SlowEngine(TestEngine):
        delay = .15
    with TestClient(create_practice_lobby(tmp_path, engine=SlowEngine())) as client:
        first = create(client, mode="local")
        second = create(client, mode="local")
        assert client.get("/api/practice-lan/rooms").json()["tables"] == 2
        for _ in range(5):
            fresh = client.get(endpoint(first)).json()["game"]
            response = client.request("DELETE", endpoint(first), headers=auth(first), json={"version": fresh["version"]})
            if response.status_code == 200:
                break
            assert response.status_code == 409
        assert response.status_code == 200
        game = ready(client, second)
        assert client.post(endpoint(second, "move"), headers=auth(first), json={"version": game["version"], "move": "D4"}).status_code == 403
        assert game["moves"] == [] and game["board"] == "." * 81
        assert client.get(endpoint(first)).status_code == 404
        assert client.get("/api/practice-lan/rooms").json()["tables"] == 1


def test_lan_access_origin_isolation_and_expired_room_reclaims_capacity(tmp_path):
    formal = tmp_path / "data"
    formal.mkdir()
    marker = formal / "tournament.sqlite3"
    marker.write_bytes(b"never open or modify formal data")
    now = [1000.0]
    with TestClient(create_practice_lobby(tmp_path / "practice-data", engine=TestEngine(), clock=lambda: now[0]),
                    base_url="http://192.168.50.20:8001", client=("192.168.50.5", 1234)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/health").json()["mode"] == "practice-lan"
        assert client.get("/api/admin/state").status_code == client.get("/api/state").status_code == 404
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
        assert client.post("/api/practice-lan/rooms", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
        assert client.post("/api/practice-lan/rooms", json={"board_size": 10}).status_code == 400
        first = create(client)
        now[0] += IDLE_SECONDS
        assert client.get("/api/practice-lan/rooms").json()["tables"] == 0
        assert client.get(endpoint(first)).status_code == 404
        assert create(client)["game"]["id"] != first["game"]["id"]
    assert marker.read_bytes() == b"never open or modify formal data"
    assert not list((tmp_path / "practice-data").rglob("*.sqlite3"))
    assert not (tmp_path / "practice-data/admin-token").exists()


def test_error_retry_keeps_accepted_move_and_double_pass_finishes(tmp_path):
    class FailingEngine(TestEngine):
        fail = False
        async def analyze(self, moves, settings):
            if self.fail:
                raise RuntimeError("temporary failure")
            return await super().analyze(moves, settings)
    engine = FailingEngine()
    with TestClient(create_practice_lobby(tmp_path, engine=engine)) as client:
        room = create(client, mode="local", forbid_best=False)
        game = ready(client, room)
        engine.fail = True
        assert client.post(endpoint(room, "move"), headers=auth(room), json={"version": game["version"], "move": "pass"}).status_code == 200
        game = ready(client, room)
        assert game["status"] == "error" and len(game["moves"]) == 1
        engine.fail = False
        assert client.post(endpoint(room, "retry"), headers=auth(room), json={"version": game["version"]}).status_code == 200
        game = ready(client, room)
        assert len(game["moves"]) == 1 and game["status"] == "playing"
        game = client.post(endpoint(room, "move"), headers=auth(room), json={"version": game["version"], "move": "pass"}).json()["game"]
        assert game["status"] == "finished" and game["result"]["reason"] == "double_pass"
        assert "remaining" not in game and "score" not in game
