from fastapi.testclient import TestClient
from server.practice import create_practice_app
from tests.test_app import TestEngine


def version(g):
    return {"game_id": g["id"], "version": g["version"]}


def test_practice_never_uses_tournament_records_and_blocks_lan(tmp_path):
    formal = tmp_path/"data"
    formal.mkdir()
    marker = formal/"tournament.sqlite3"
    marker.write_bytes(b"formal data must remain untouched")
    app = create_practice_app(tmp_path/"practice", engine=TestEngine())
    with TestClient(app) as client:
        assert client.get("/api/health").json()["mode"] == "practice"
        assert client.get("/api/state").status_code == 404
        assert client.get("/api/admin/state").status_code == 404
        assert client.post("/api/practice/new", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
        assert client.post("/api/practice/new", json={"board_size": 10}).status_code == 400
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 403
    with TestClient(create_practice_app(tmp_path/"other", engine=TestEngine()), client=("192.168.1.5", 1234)) as remote:
        assert remote.get("/").status_code == 403
    assert marker.read_bytes() == b"formal data must remain untouched"
    assert not list((tmp_path/"practice").rglob("*.sqlite3")) and not (tmp_path/"practice/admin-token").exists()


def test_two_people_alternate_legally_with_undo_sgf_and_stale_protection(tmp_path):
    with TestClient(create_practice_app(tmp_path, engine=TestEngine())) as client:
        g = client.post("/api/practice/new", json={"board_size": 9, "komi": 7.5}).json()["game"]
        assert g["turn"] == "B" and g["settings"]["komi"] == 7.5
        assert client.post("/api/practice/move", json={**version(g), "move": "a09"}).status_code == 400
        old = version(g)
        g = client.post("/api/practice/move", json={**old, "move": g["candidates"][1]["move"]}).json()["game"]
        assert g["turn"] == "W" and len(g["moves"]) == 1
        assert client.post("/api/practice/move", json={**old, "move": "D4"}).status_code == 409
        g = client.post("/api/practice/move", json={**version(g), "move": g["candidates"][1]["move"]}).json()["game"]
        assert g["turn"] == "B" and len(g["moves"]) == 2
        saved = client.get("/api/practice/sgf").text
        assert "KM[7.5]" in saved and ";B[" in saved and ";W[" in saved
        g = client.post("/api/practice/undo", json=version(g)).json()["game"]
        assert g["turn"] == "W" and len(g["moves"]) == 1
        ended = client.post("/api/practice/finish", json=version(g)).json()["game"]
        assert ended["status"] == "finished"
        assert client.post("/api/practice/move", json={**version(ended), "move": "D4"}).status_code == 409
        fresh = client.post("/api/practice/new", json={"board_size": 9}).json()["game"]
        assert fresh["id"] != g["id"]
        assert client.post("/api/practice/move", json={"game_id": g["id"], "version": fresh["version"], "move": "D4"}).status_code == 409


def test_double_pass_ends_practice_without_declaring_formal_winner(tmp_path):
    with TestClient(create_practice_app(tmp_path, engine=TestEngine())) as client:
        g = client.post("/api/practice/new", json={"board_size": 9, "forbid_best": False}).json()["game"]
        assert g["forbidden"] is None
        for _ in range(2):
            g = client.post("/api/practice/move", json={**version(g), "move": "pass"}).json()["game"]
        assert g["status"] == "finished" and "result" not in g
        assert "remaining" not in g and "score" not in g


def test_failed_analysis_preserves_moves_and_can_retry(tmp_path):
    class TemporaryFailure(TestEngine):
        fail = False
        async def analyze(self, moves, settings):
            if self.fail:
                raise RuntimeError("temporary")
            return await super().analyze(moves, settings)
    engine = TemporaryFailure()
    with TestClient(create_practice_app(tmp_path, engine=engine)) as client:
        g = client.post("/api/practice/new", json={"board_size": 9}).json()["game"]
        engine.fail = True
        g = client.post("/api/practice/move", json={**version(g), "move": g["candidates"][1]["move"]}).json()["game"]
        assert g["status"] == "error" and len(g["moves"]) == 1
        engine.fail = False
        retry = client.post("/api/practice/retry", json=version(g)).json()["game"]
        assert retry["status"] == "playing" and retry["moves"] == g["moves"]
