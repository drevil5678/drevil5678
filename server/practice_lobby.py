"""LAN practice rooms and real KataGo opponents, isolated from tournament data."""
import asyncio
import copy
import ipaddress
import math
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .credentials import matches
from .engine import KataGo
from .go import IllegalMove, candidates, decode, encode, play, sgf
from .lan import network_info
from .practice import PracticeSettings

MAX_TABLES = 15
IDLE_SECONDS = 30 * 60


class RoomInput(PracticeSettings):
    name: str = Field(default="自由練習", min_length=1, max_length=40)
    player_name: str = Field(default="玩家", min_length=1, max_length=30)
    mode: str = "pvp"
    human_color: str = "B"


class JoinInput(BaseModel):
    player_name: str = Field(default="玩家", min_length=1, max_length=30)


class VersionInput(BaseModel):
    version: int


class MoveInput(VersionInput):
    move: str = Field(max_length=5)


class UndoResponse(VersionInput):
    accept: bool


def create_practice_lobby(runtime, engine=None, network=None, clock=time.time):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    ai = engine or KataGo(runtime)
    rooms, tasks = {}, {}
    registry = asyncio.Lock()
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    binding = network or {"host": "0.0.0.0", "port": 8001}

    def discard(ident):
        rooms.pop(ident, None)
        task = tasks.pop(ident, None)
        if task:
            task.cancel()

    def purge():
        for ident, room in list(rooms.items()):
            if clock() - room["last_activity"] >= IDLE_SECONDS:
                discard(ident)

    async def reaper():
        while True:
            await asyncio.sleep(15)
            async with registry:
                purge()

    @asynccontextmanager
    async def lifespan(_):
        await ai.start()
        janitor = asyncio.create_task(reaper())
        try:
            yield
        finally:
            running = list(tasks.values()) + [janitor]
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            await ai.close()

    app = FastAPI(title="自由練習大廳", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ai = ai

    @app.middleware("http")
    async def guard(request: Request, call_next):
        hostname = request.url.hostname or ""
        allowed = hostname == "localhost" or (hostname == "testserver" and request.client and request.client.host == "testclient")
        try:
            address = ipaddress.ip_address(hostname)
            allowed = address.is_private or address.is_loopback
        except ValueError:
            pass
        if not allowed:
            return Response("請使用練習主機的區域網路網址", status_code=403)
        origin = request.headers.get("origin")
        if request.method in ("POST", "DELETE") and origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return Response("跨來源操作遭拒絕", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    def room_by_id(ident):
        purge()
        room = rooms.get(ident)
        if room is None:
            raise HTTPException(404, "這桌已收桌或逾時，請回大廳")
        return room

    def live(room):
        if rooms.get(room["id"]) is not room:
            raise HTTPException(404, "這桌已收桌，請回大廳")

    def identity(room, authorization):
        token = (authorization or "").removeprefix("Bearer ")
        owner = matches(room["owner_token"], token)
        color = next((c for c, seat in room["seats"].items()
                      if seat.get("token") and matches(seat["token"], token)), None)
        return {"owner": owner, "color": "both" if owner and room["mode"] == "local" else color}

    def participant(room, authorization):
        who = identity(room, authorization)
        if not who["color"]:
            raise HTTPException(403, "請先入座或輸入本桌操作碼")
        return who

    def version(room, body):
        live(room)
        if body.version != room["version"]:
            raise HTTPException(409, "棋局已更新，請確認最新棋盤後重試")

    def public(room, authorization=None):
        game = {key: copy.deepcopy(room[key]) for key in (
            "id", "name", "mode", "settings", "board", "moves", "turn", "version", "status",
            "candidates", "forbidden", "error", "result", "undo_pending", "last_activity")}
        game["candidates"] = game["candidates"][:5]
        game["seats"] = {c: {"name": s["name"], "occupied": bool(s.get("token") or s.get("computer")),
                             "computer": bool(s.get("computer"))} for c, s in room["seats"].items()}
        game["viewer"] = identity(room, authorization)
        return {"game": game}

    def touch(room):
        room["last_activity"] = clock()

    def ended(room, reason, winner=None):
        room.update(status="finished", candidates=[], forbidden=None, error="", undo_pending=None,
                    result={"reason": reason, "winner": winner}, version=room["version"] + 1)
        touch(room)
        task = tasks.get(room["id"])
        if task and task is not asyncio.current_task():
            task.cancel()

    def apply_move(room, chosen):
        cfg = room["settings"]
        board, _ = play(room["board"], room["turn"], chosen, cfg["board_size"], room["history"], cfg["rules"])
        room["moves"].append([room["turn"], chosen])
        room["history"].append(board)
        room.update(board=board, turn="W" if room["turn"] == "B" else "B", version=room["version"] + 1)
        touch(room)
        if len(room["moves"]) >= 2 and all(m[1] == "pass" for m in room["moves"][-2:]):
            ended(room, "double_pass")

    def analyzing(room):
        room.update(status="analyzing", candidates=[], forbidden=None, error="", undo_pending=None,
                    version=room["version"] + 1)

    async def pump(room):
        while True:
            async with room["lock"]:
                if rooms.get(room["id"]) is not room or room["status"] != "analyzing":
                    return
                stamp = room["version"]
                moves = copy.deepcopy(room["moves"])
                cfg = room["settings"].copy()
            try:
                data = await ai.analyze(moves, cfg)
                async with room["lock"]:
                    if rooms.get(room["id"]) is not room or stamp != room["version"] or room["status"] != "analyzing":
                        return
                    options = candidates(room, cfg, data["moveInfos"])
                    if not options:
                        raise RuntimeError("AI 未提供合法選點")
                    forbidden = options[0]["move"] if cfg["forbid_best"] else None
                    if room["seats"][room["turn"]].get("computer"):
                        chosen = next((o["move"] for o in options if o["move"] != forbidden), None)
                        if chosen is None:
                            for chosen in [encode(i, cfg["board_size"]) for i in range(len(room["board"]))] + ["pass"]:
                                if chosen == forbidden:
                                    continue
                                try:
                                    play(room["board"], room["turn"], chosen, cfg["board_size"], room["history"], cfg["rules"])
                                    break
                                except IllegalMove:
                                    continue
                            else:
                                raise RuntimeError("沒有合法可選點")
                        apply_move(room, chosen)
                        if room["status"] == "finished":
                            return
                        analyzing(room)
                        continue
                    room.update(candidates=options, forbidden=forbidden, status="playing", error="",
                                version=room["version"] + 1)
                    touch(room)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                async with room["lock"]:
                    if rooms.get(room["id"]) is room and room["status"] == "analyzing":
                        room.update(status="error", candidates=[], forbidden=None,
                                    error="AI 分析暫時失敗，已保留棋著；請按重新分析。", version=room["version"] + 1)
                return

    def schedule(room):
        previous = tasks.get(room["id"])
        if previous and not previous.done():
            previous.cancel()
        analyzing(room)
        task = asyncio.create_task(pump(room))
        tasks[room["id"]] = task

        def done(completed):
            if tasks.get(room["id"]) is completed:
                tasks.pop(room["id"], None)
        task.add_done_callback(done)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "ai_ready": not ai.failure, "mode": "practice-lan", "max_tables": MAX_TABLES}

    @app.get("/api/practice-lan/rooms")
    async def lobby():
        async with registry:
            purge()
            listing = [{"id": r["id"], "name": r["name"], "mode": r["mode"], "status": r["status"],
                        "board_size": r["settings"]["board_size"], "moves": len(r["moves"]),
                        "seats": {c: {"name": s["name"], "occupied": bool(s.get("token") or s.get("computer"))}
                                  for c, s in r["seats"].items()}} for r in rooms.values()]
            return {"rooms": listing, "tables": len(rooms), "max_tables": MAX_TABLES, "idle_minutes": IDLE_SECONDS // 60}

    @app.get("/api/practice-lan/network")
    async def network_addresses():
        info = network_info(binding["host"], binding["port"])
        return {"urls": [item["url"].split("/#", 1)[0] + "/" for item in info["interfaces"]]}

    @app.post("/api/practice-lan/rooms")
    async def create_room(body: RoomInput):
        if body.mode not in ("pvp", "local", "ai") or body.human_color not in ("B", "W"):
            raise HTTPException(400, "請選擇對戰模式與棋色")
        if body.board_size not in (9, 13, 19) or body.rules not in ("chinese", "japanese") or not math.isfinite(body.komi):
            raise HTTPException(400, "請選擇有效棋盤、貼目與規則")
        if not body.name.strip() or not body.player_name.strip():
            raise HTTPException(400, "桌名與暱稱不可空白")
        if ai.failure:
            raise HTTPException(503, "練習 AI 尚未就緒，請稍後再試")
        async with registry:
            purge()
            if len(rooms) >= MAX_TABLES:
                raise HTTPException(429, "目前 15 桌已滿，請等其他玩家收桌後再開桌")
            token = secrets.token_urlsafe(32)
            other = "W" if body.human_color == "B" else "B"
            seats = {body.human_color: {"name": body.player_name.strip(), "token": token},
                     other: {"name": "等待入座", "token": None}}
            if body.mode == "ai":
                seats[other].update(name="電腦", computer=True)
            elif body.mode == "local":
                seats[other].update(name="同機玩家", token=token)
            board = "." * body.board_size ** 2
            room = {"id": uuid.uuid4().hex[:16], "name": body.name.strip(), "mode": body.mode,
                    "owner_token": token, "seats": seats,
                    "settings": {**body.model_dump(include={"board_size", "komi", "rules", "forbid_best"}), "visits": 128, "allow_pass": True},
                    "board": board, "history": [board], "moves": [], "turn": "B", "version": 0,
                    "status": "waiting", "candidates": [], "forbidden": None, "error": "", "result": None,
                    "undo_pending": None, "last_activity": clock(), "lock": asyncio.Lock()}
            rooms[room["id"]] = room
            if body.mode != "pvp":
                schedule(room)
            return {**public(room, "Bearer " + token), "token": token}

    @app.get("/api/practice-lan/rooms/{ident}")
    async def state(ident: str, authorization: str | None = Header(None)):
        room = room_by_id(ident)
        async with room["lock"]:
            live(room)
            return public(room, authorization)

    @app.post("/api/practice-lan/rooms/{ident}/join")
    async def join(ident: str, body: JoinInput, authorization: str | None = Header(None)):
        room = room_by_id(ident)
        async with room["lock"]:
            live(room)
            if identity(room, authorization)["color"]:
                return public(room, authorization)
            if not body.player_name.strip():
                raise HTTPException(400, "暱稱不可空白")
            if room["mode"] != "pvp" or room["status"] != "waiting":
                raise HTTPException(409, "這桌已滿或已開始")
            color = next((c for c, s in room["seats"].items() if not s.get("token")), None)
            if color is None:
                raise HTTPException(409, "這桌已滿")
            token = secrets.token_urlsafe(32)
            room["seats"][color].update(name=body.player_name.strip(), token=token)
            touch(room)
            schedule(room)
            return {**public(room, "Bearer " + token), "token": token}

    @app.post("/api/practice-lan/rooms/{ident}/move")
    async def move(ident: str, body: MoveInput, authorization: str | None = Header(None)):
        room = room_by_id(ident)
        async with room["lock"]:
            version(room, body)
            who = participant(room, authorization)
            if who["color"] not in ("both", room["turn"]):
                raise HTTPException(403, "尚未輪到你的棋色")
            if room["status"] != "playing" or room["undo_pending"]:
                raise HTTPException(409, "目前不能落子，請等待分析或悔棋回應")
            try:
                pos = decode(body.move, room["settings"]["board_size"])
                chosen = "pass" if pos is None else encode(pos, room["settings"]["board_size"])
                if chosen == room["forbidden"]:
                    raise IllegalMove("此桌禁止選擇 AI 首選")
                apply_move(room, chosen)
            except IllegalMove as exc:
                raise HTTPException(400, str(exc)) from exc
            if room["status"] != "finished":
                schedule(room)
            return public(room, authorization)

    def undo_moves(room, count):
        del room["moves"][-count:]
        del room["history"][-count:]
        room.update(board=room["history"][-1], turn="B" if len(room["moves"]) % 2 == 0 else "W", result=None)
        touch(room)
        schedule(room)

    @app.post("/api/practice-lan/rooms/{ident}/undo")
    async def undo(ident: str, body: VersionInput, authorization: str | None = Header(None)):
        room = room_by_id(ident)
        async with room["lock"]:
            version(room, body)
            who = participant(room, authorization)
            if room["status"] not in ("playing", "finished") or not room["moves"] or room["undo_pending"]:
                raise HTTPException(409, "目前不能悔棋")
            if room["mode"] == "pvp":
                room.update(undo_pending={"color": who["color"]}, version=room["version"] + 1)
                touch(room)
            else:
                count = 1
                if room["mode"] == "ai":
                    human = who["color"]
                    index = next((i for i in range(len(room["moves"]) - 1, -1, -1) if room["moves"][i][0] == human), None)
                    if index is None:
                        raise HTTPException(409, "你尚未落子，沒有可悔棋的回合")
                    count = len(room["moves"]) - index
                undo_moves(room, count)
            return public(room, authorization)

    @app.post("/api/practice-lan/rooms/{ident}/undo-response")
    async def undo_response(ident: str, body: UndoResponse, authorization: str | None = Header(None)):
        room = room_by_id(ident)
        async with room["lock"]:
            version(room, body)
            who = participant(room, authorization)
            pending = room["undo_pending"]
            if not pending:
                raise HTTPException(409, "目前沒有悔棋申請")
            if body.accept and who["color"] == pending["color"]:
                raise HTTPException(403, "需由對手同意悔棋")
            if body.accept:
                undo_moves(room, 1)
            else:
                room.update(undo_pending=None, version=room["version"] + 1)
                touch(room)
            return public(room, authorization)

    @app.post("/api/practice-lan/rooms/{ident}/retry")
    async def retry(ident: str, body: VersionInput, authorization: str | None = Header(None)):
        room = room_by_id(ident)
        async with room["lock"]:
            version(room, body)
            participant(room, authorization)
            if room["status"] != "error":
                raise HTTPException(409, "目前無需重新分析")
            touch(room)
            schedule(room)
            return public(room, authorization)

    @app.post("/api/practice-lan/rooms/{ident}/finish")
    async def finish(ident: str, body: VersionInput, authorization: str | None = Header(None)):
        room = room_by_id(ident)
        async with room["lock"]:
            version(room, body)
            who = participant(room, authorization)
            if room["status"] == "finished":
                raise HTTPException(409, "本局已結束")
            manual = room["mode"] == "local" or room["status"] == "waiting"
            winner = None if manual else "W" if who["color"] == "B" else "B"
            ended(room, "manual" if manual else "resign", winner)
            return public(room, authorization)

    @app.delete("/api/practice-lan/rooms/{ident}")
    async def close(ident: str, body: VersionInput, authorization: str | None = Header(None)):
        room = room_by_id(ident)
        async with room["lock"]:
            version(room, body)
            if not identity(room, authorization)["owner"]:
                raise HTTPException(403, "只有開桌者可以收桌")
            async with registry:
                discard(ident)
            return {"closed": True}

    @app.get("/api/practice-lan/rooms/{ident}/sgf")
    async def export(ident: str):
        room = room_by_id(ident)
        async with room["lock"]:
            live(room)
            return Response(sgf(room, room["settings"]), media_type="application/x-go-sgf",
                            headers={"Content-Disposition": f'attachment; filename="practice-{ident}.sgf"'})

    @app.get("/")
    async def index():
        return FileResponse(root / "web/practice-lobby.html")

    app.mount("/static", StaticFiles(directory=root / "web"), name="static")
    return app
