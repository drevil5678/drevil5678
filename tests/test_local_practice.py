"""Exercise practice rooms as private same-computer sessions on the main server."""
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.local_practice import IDLE_SECONDS, install_local_practice
from tests.test_app import TestEngine

PRACTICE_TEAMS = [{"id": "team-a", "name": "練習甲隊", "play_code": "ABCD-EFGH-JKLM"}]

def login(client):
    response = client.post("/api/local-practice/login", json={"code": PRACTICE_TEAMS[0]["play_code"]})
    assert response.status_code == 200, response.text
    return response


@contextmanager
def practice(engine=None, clock=time.time):
    ai = engine or TestEngine()
    manager = None

    @asynccontextmanager
    async def lifespan(app):
        await ai.start()
        await manager.start()
        try:
            yield
        finally:
            await manager.close()
            await ai.close()

    app = FastAPI(lifespan=lifespan)
    manager = install_local_practice(app, ai, Path(__file__).resolve().parents[1], clock=clock, teams=lambda: PRACTICE_TEAMS, enabled=lambda: True)
    with TestClient(app, base_url="http://192.168.50.20:8000", client=("192.168.50.5", 1234)) as client:
        yield client, app


def endpoint(room, action=""):
    return "/api/local-practice/rooms/" + room["game"]["id"] + ("/" + action if action else "")


def create(client, **settings):
    response = client.post("/api/local-practice/rooms", json={"board_size": 9, **settings})
    assert response.status_code == 200, response.text
    return response.json()


def ready(client, room, moves=None):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = client.get(endpoint(room))
        assert response.status_code == 200, response.text
        game = response.json()["game"]
        if game["status"] in ("playing", "finished", "error") and (moves is None or len(game["moves"]) == moves):
            return game
        time.sleep(.005)
    raise AssertionError("room analysis did not complete")


@contextmanager
def other_browser(app, owner, ip="192.168.50.5"):
    # Use one ASGI lifespan/event loop but independent HTTP cookie jars/IPs.
    client = TestClient(app, base_url=str(owner.base_url), client=(ip, 4567))
    client.portal = owner.portal
    try:
        login(client)
        yield client
    finally:
        client.close()


def test_bootstrap_private_cookie_ip_binding_and_no_remote_join():
    with practice() as (client, app):
        assert client.post("/api/local-practice/rooms", json={}).status_code == 401
        assert client.get("/api/local-practice/rooms").status_code == 401
        response = login(client)
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "samesite=strict" in response.headers["set-cookie"].lower()
        assert client.post("/api/local-practice/rooms", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
        assert client.post("/api/local-practice/rooms", json={"mode": "pvp"}).status_code == 400
        assert client.post("/api/local-practice/rooms", json={"board_size": 10}).status_code == 400
        room = create(client)
        own = ready(client, room)
        with other_browser(app, client) as other:
            assert other.get("/api/local-practice/rooms").json()["rooms"] == []
            assert other.get(endpoint(room)).status_code == 404
            assert other.get(endpoint(room, "sgf")).status_code == 404
            assert other.post(endpoint(room, "join"), json={}).status_code == 404
            assert other.post(endpoint(room, "move"), json={"version": own["version"], "move": "D4"}).status_code == 404
            assert other.request("DELETE", endpoint(room), json={"version": own["version"]}).status_code == 404
            other_room = create(other)
            assert other_room["game"]["id"] not in {r["id"] for r in client.get("/api/local-practice/rooms").json()["rooms"]}
            assert client.get("/api/local-practice/rooms").json()["tables"] == 2
        with other_browser(app, client, "192.168.50.6") as copied:
            copied.cookies.update(client.cookies)
            assert copied.get(endpoint(room), headers={"X-Forwarded-For": "192.168.50.5"}).status_code == 404
            assert copied.post(endpoint(room, "finish"), json={"version": own["version"]}).status_code == 404
        assert client.post(endpoint(room, "finish"), json={"version": own["version"]}, headers={"Origin": "http://evil.example"}).status_code == 403
        listing = client.get("/api/local-practice/rooms").text
        assert "owner" not in listing and "cookie" not in listing


def test_same_browser_can_only_create_one_table_even_concurrently_or_after_finish():
    with practice() as (client, app):
        login(client)
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(lambda _: client.post("/api/local-practice/rooms", json={"board_size": 9}), range(24)))
        assert [r.status_code for r in responses].count(200) == 1
        assert [r.status_code for r in responses].count(409) == 23
        listing = client.get("/api/local-practice/rooms").json()
        assert listing["tables"] == len(listing["rooms"]) == 1
        first = next(r.json() for r in responses if r.status_code == 200)
        with other_browser(app, client) as another_tab:
            another_tab.cookies.update(client.cookies)
            assert another_tab.get("/api/local-practice/rooms").json()["rooms"][0]["id"] == first["game"]["id"]
            assert another_tab.post("/api/local-practice/rooms", json={}).status_code == 409
        game = ready(client, first)
        game = client.post(endpoint(first, "finish"), json={"version": game["version"]}).json()["game"]
        assert game["status"] == "finished"
        assert client.post("/api/local-practice/rooms", json={}).status_code == 409
        assert client.request("DELETE", endpoint(first), json={"version": game["version"]}).status_code == 200
        assert client.get(endpoint(first)).status_code == 404
        assert create(client)["game"]["id"] != first["game"]["id"]
        assert client.get("/api/local-practice/rooms").json()["tables"] == 1


def test_global_capacity_is_atomic_across_distinct_browsers_without_exposing_limit_in_errors():
    with practice() as (client, app), ExitStack() as contexts:
        browsers = [client] + [contexts.enter_context(other_browser(app, client)) for _ in range(19)]
        for browser in browsers:
            login(browser)
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(executor.map(lambda browser: browser.post("/api/local-practice/rooms", json={"board_size": 9}), browsers))
        assert [r.status_code for r in responses].count(200) == 15
        assert [r.status_code for r in responses].count(429) == 5
        assert all("15" not in r.text for r in responses if r.status_code == 429)
        assert client.get("/api/local-practice/rooms").json()["tables"] == 15
        owner_index = next(i for i, r in enumerate(responses) if r.status_code == 200)
        owner, room = browsers[owner_index], responses[owner_index].json()
        game = ready(owner, room)
        assert owner.request("DELETE", endpoint(room), json={"version": game["version"]}).status_code == 200
        waiting_index = next(i for i, r in enumerate(responses) if r.status_code == 429)
        assert create(browsers[waiting_index])["game"]["id"] != room["game"]["id"]


def test_same_computer_alternation_forbidden_stale_undo_and_sgf():
    with practice() as (client, _):
        login(client)
        room = create(client)
        game = ready(client, room)
        assert game["mode"] == "local" and game["turn"] == "B"
        assert game["settings"]["komi"] == 7.5
        assert game["moves_played"] == 0 and game["move_number"] == 1
        disguised = game["forbidden"][0].lower() + "0" + game["forbidden"][1:]
        assert client.post(endpoint(room, "move"), json={"version": game["version"], "move": disguised}).status_code == 400
        old = game["version"]
        assert client.post(endpoint(room, "move"), json={"version": old, "move": game["candidates"][1]["move"]}).status_code == 200
        game = ready(client, room, moves=1)
        assert game["turn"] == "W"
        assert client.post(endpoint(room, "move"), json={"version": old, "move": "D4"}).status_code == 409
        assert client.post(endpoint(room, "move"), json={"version": game["version"], "move": game["candidates"][1]["move"]}).status_code == 200
        game = ready(client, room, moves=2)
        assert game["turn"] == "B" and game["moves"][0][0] == "B" and game["moves"][1][0] == "W"
        assert game["moves_played"] == 2 and game["move_number"] == 3
        saved = client.get(endpoint(room, "sgf")).text
        assert "KM[7.5]" in saved and ";B[" in saved and ";W[" in saved
        assert client.post(endpoint(room, "undo"), json={"version": game["version"]}).status_code == 200
        game = ready(client, room, moves=1)
        assert game["turn"] == "W"
        assert "remaining" not in game and "streak" not in game


def test_computer_black_or_white_and_undo_returns_same_human_turn():
    with practice() as (client, _):
        login(client)
        for color in ("B", "W"):
            room = create(client, mode="ai", human_color=color)
            game = ready(client, room, moves=0 if color == "B" else 1)
            assert game["turn"] == color and game["human_color"] == color
            if color == "W":
                assert game["moves"][0] == ["B", "B9"]  # AI cannot play forbidden A9.
                assert client.post(endpoint(room, "undo"), json={"version": game["version"]}).status_code == 409
            assert client.post(endpoint(room, "move"), json={"version": game["version"], "move": game["candidates"][1]["move"]}).status_code == 200
            game = ready(client, room, moves=2 if color == "B" else 3)
            assert game["turn"] == color
            assert client.post(endpoint(room, "undo"), json={"version": game["version"]}).status_code == 200
            game = ready(client, room, moves=0 if color == "B" else 1)
            assert game["turn"] == color
            assert client.request("DELETE", endpoint(room), json={"version": game["version"]}).status_code == 200


def test_ai_failure_retry_preserves_accepted_move_and_double_pass_scores_board():
    class FailingEngine(TestEngine):
        fail = False

        async def analyze(self, moves, settings):
            if self.fail:
                raise RuntimeError("temporary failure")
            return await super().analyze(moves, settings)

    engine = FailingEngine()
    with practice(engine) as (client, _):
        login(client)
        room = create(client, forbid_best=False)
        game = ready(client, room)
        engine.fail = True
        assert client.post(endpoint(room, "move"), json={"version": game["version"], "move": "pass"}).status_code == 200
        game = ready(client, room)
        assert game["status"] == "error" and game["moves"] == [["B", "pass"]]
        engine.fail = False
        assert client.post(endpoint(room, "retry"), json={"version": game["version"]}).status_code == 200
        game = ready(client, room)
        assert game["status"] == "playing" and game["moves"] == [["B", "pass"]]
        game = client.post(endpoint(room, "move"), json={"version": game["version"], "move": "pass"}).json()["game"]
        assert game["status"] == "finished"
        assert game["result"]["reason"] == "double_pass" and game["result"]["winner"] == "W"
        assert game["result"]["score"]["black"] == 0
        assert game["result"]["score"]["white"] == 7.5
        assert client.post(endpoint(room, "move"), json={"version": game["version"], "move": "D4"}).status_code == 409


def test_expiry_and_closing_during_analysis_cannot_resurrect_room():
    class SlowEngine(TestEngine):
        delay = .15

    now = [1000.0]
    with practice(SlowEngine(), clock=lambda: now[0]) as (client, _):
        login(client)
        closed = create(client)
        current = client.get(endpoint(closed)).json()["game"]
        assert current["status"] == "analyzing"
        assert client.request("DELETE", endpoint(closed), json={"version": current["version"]}).status_code == 200
        expired = create(client)
        now[0] += IDLE_SECONDS
        assert client.get("/api/local-practice/rooms").json()["tables"] == 0
        time.sleep(.2)
        assert client.get(endpoint(closed)).status_code == client.get(endpoint(expired)).status_code == 404
        assert client.get("/api/local-practice/rooms").json()["rooms"] == []
        replacement = create(client)
        assert replacement["game"]["id"] not in (closed["game"]["id"], expired["game"]["id"])
        assert ready(client, replacement)["moves"] == []
