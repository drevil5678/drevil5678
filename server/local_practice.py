"""Same-browser practice tables on the tournament server, without tournament state."""
import asyncio
import copy
import math
import secrets
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .go import IllegalMove, candidates, decode, encode, play, sgf
from .practice import PracticeSettings
from .scoring import score_position

MAX_TABLES = 15
IDLE_SECONDS = 30 * 60
COOKIE_NAME = "yanghai_local_practice"
COOKIE_PATH = "/api/local-practice"


class LocalRoomInput(PracticeSettings):
    name: str = Field(default="自由練習", min_length=1, max_length=40)
    mode: str = "local"
    human_color: str = "B"


class LocalVersion(BaseModel):
    version: int


class LocalMove(LocalVersion):
    move: str = Field(max_length=5)


class LocalPracticeManager:
    def __init__(self, ai, clock=time.time):
        self.ai = ai
        self.clock = clock
        self.rooms = {}
        self.sessions = {}
        self.tasks = {}
        self.registry = asyncio.Lock()
        self.janitor = None

    async def start(self):
        if self.janitor is None or self.janitor.done():
            self.janitor = asyncio.create_task(self.reap())

    async def close(self):
        running = list(self.tasks.values())
        if self.janitor:
            running.append(self.janitor)
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        self.tasks.clear()
        self.rooms.clear()
        self.sessions.clear()
        self.janitor = None

    def discard(self, ident):
        self.rooms.pop(ident, None)
        task = self.tasks.pop(ident, None)
        if task:
            task.cancel()

    def purge(self):
        now = self.clock()
        for ident, room in list(self.rooms.items()):
            if now - room["last_activity"] >= IDLE_SECONDS:
                self.discard(ident)
        active = {r["owner"] for r in self.rooms.values()}
        for token, session in list(self.sessions.items()):
            if token not in active and now - session["seen"] >= 24 * 60 * 60:
                self.sessions.pop(token, None)

    async def reap(self):
        while True:
            await asyncio.sleep(15)
            async with self.registry:
                self.purge()

    @staticmethod
    def peer(request):
        return request.client.host if request.client else ""

    @staticmethod
    def guard(request):
        origin = request.headers.get("origin")
        if ((origin and origin.rstrip("/") != str(request.base_url).rstrip("/")) or
                request.headers.get("sec-fetch-site") == "cross-site"):
            raise HTTPException(403, "跨來源操作遭拒絕")

    def owner(self, request, required=True):
        token = request.cookies.get(COOKIE_NAME, "")
        session = self.sessions.get(token)
        if session and session["host"] == self.peer(request):
            session["seen"] = self.clock()
            return token
        if required:
            raise HTTPException(403, "請從練習頁面開桌，並使用同一台電腦與瀏覽器操作")
        return None

    def bootstrap(self, request, response):
        token = self.owner(request, required=False)
        if not token:
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {"host": self.peer(request), "seen": self.clock()}
            response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                                path=COOKIE_PATH, max_age=24 * 60 * 60)
        return token

    def room(self, ident, request):
        # Do not disclose table existence to another browser or network peer.
        token = self.owner(request, required=False)
        self.purge()
        room = self.rooms.get(ident)
        if room is None or not token or room["owner"] != token or room["host"] != self.peer(request):
            raise HTTPException(404, "找不到這桌；請使用原本開桌的電腦與瀏覽器")
        return room

    def live(self, room):
        if self.rooms.get(room["id"]) is not room:
            raise HTTPException(404, "這桌已收桌或逾時，請重新開桌")

    def version(self, room, body):
        self.live(room)
        if body.version != room["version"]:
            raise HTTPException(409, "棋局已更新，請確認最新棋盤後重試")

    def public(self, room):
        fields = ("id", "name", "mode", "human_color", "settings", "board", "moves", "turn", "version", "status",
                  "candidates", "forbidden", "error", "result", "last_activity")
        game = {key: copy.deepcopy(room[key]) for key in fields}
        game["candidates"] = game["candidates"][:5]
        game["move_number"] = len(room["moves"]) + 1
        game["moves_played"] = len(room["moves"])
        game["human_turn"] = room["mode"] == "local" or room["turn"] == room["human_color"]
        return {"game": game}

    def touch(self, room):
        room["last_activity"] = self.clock()

    def ended(self, room, reason, winner=None, score=None):
        result = {"reason": reason, "winner": winner}
        if score is not None:
            result["score"] = score
        room.update(status="finished", candidates=[], forbidden=None, error="", result=result,
                    version=room["version"] + 1)
        self.touch(room)
        task = self.tasks.get(room["id"])
        if task and task is not asyncio.current_task():
            task.cancel()

    def apply_move(self, room, chosen):
        cfg = room["settings"]
        color = room["turn"]
        board, captured = play(room["board"], color, chosen, cfg["board_size"], room["history"], cfg["rules"])
        captures = room["capture_history"][-1].copy()
        captures[color] += captured
        room["moves"].append([color, chosen])
        room["history"].append(board)
        room["capture_history"].append(captures)
        room.update(board=board, turn="W" if color == "B" else "B", version=room["version"] + 1)
        self.touch(room)
        if len(room["moves"]) >= 2 and all(m[1] == "pass" for m in room["moves"][-2:]):
            score = score_position(board, cfg["board_size"], cfg["komi"], cfg["rules"], captures)
            self.ended(room, "double_pass", score["winner"], score)

    def analyzing(self, room):
        room.update(status="analyzing", candidates=[], forbidden=None, error="", version=room["version"] + 1)

    async def pump(self, room):
        while True:
            async with room["lock"]:
                if self.rooms.get(room["id"]) is not room or room["status"] != "analyzing":
                    return
                stamp, moves, cfg = room["version"], copy.deepcopy(room["moves"]), room["settings"].copy()
            try:
                # Only the shared engine controls its analysis queue. No registry lock is held here.
                data = await self.ai.analyze(moves, cfg)
                async with room["lock"]:
                    if self.rooms.get(room["id"]) is not room or stamp != room["version"] or room["status"] != "analyzing":
                        return
                    options = candidates(room, cfg, data["moveInfos"])
                    if not options:
                        raise RuntimeError("AI 未提供合法選點")
                    forbidden = options[0]["move"] if cfg["forbid_best"] else None
                    if room["mode"] == "ai" and room["turn"] != room["human_color"]:
                        chosen = next((o["move"] for o in options if o["move"] != forbidden), None)
                        if chosen is None:
                            for move in [encode(i, cfg["board_size"]) for i in range(len(room["board"]))] + ["pass"]:
                                if move == forbidden:
                                    continue
                                try:
                                    play(room["board"], room["turn"], move, cfg["board_size"], room["history"], cfg["rules"])
                                    chosen = move
                                    break
                                except IllegalMove:
                                    continue
                        if chosen is None:
                            raise RuntimeError("沒有合法可選點")
                        self.apply_move(room, chosen)
                        if room["status"] == "finished":
                            return
                        self.analyzing(room)
                        continue
                    room.update(candidates=options, forbidden=forbidden, status="playing", error="",
                                version=room["version"] + 1)
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                async with room["lock"]:
                    if self.rooms.get(room["id"]) is room and stamp == room["version"] and room["status"] == "analyzing":
                        room.update(status="error", candidates=[], forbidden=None,
                                    error="AI 分析暫時失敗，已保留棋著；請按重新分析。", version=room["version"] + 1)
                return

    def schedule(self, room):
        previous = self.tasks.get(room["id"])
        if previous and not previous.done():
            previous.cancel()
        self.analyzing(room)
        task = asyncio.create_task(self.pump(room))
        self.tasks[room["id"]] = task

        def done(completed):
            if self.tasks.get(room["id"]) is completed:
                self.tasks.pop(room["id"], None)
        task.add_done_callback(done)


def install_local_practice(app: FastAPI, ai, root, clock=time.time):
    """Install routes; the caller awaits manager.start/close in its own lifespan."""
    manager = LocalPracticeManager(ai, clock)
    root = Path(root)
    base = "/api/local-practice/rooms"

    @app.get("/local-practice", include_in_schema=False)
    async def practice_page():
        return FileResponse(root / "web/local-practice.html", headers={"Cache-Control": "no-store"})

    @app.get(base)
    async def listing(request: Request, response: Response):
        manager.guard(request)
        async with manager.registry:
            manager.purge()
            token = manager.bootstrap(request, response)
            rooms = [{"id": r["id"], "name": r["name"], "mode": r["mode"], "status": r["status"],
                      "board_size": r["settings"]["board_size"], "moves": len(r["moves"])}
                     for r in manager.rooms.values() if r["owner"] == token and r["host"] == manager.peer(request)]
            response.headers["Cache-Control"] = "no-store"
            return {"rooms": rooms, "tables": len(manager.rooms), "max_tables": MAX_TABLES,
                    "idle_minutes": IDLE_SECONDS // 60}

    @app.post(base)
    async def create(request: Request, body: LocalRoomInput):
        manager.guard(request)
        token = manager.owner(request)
        if body.mode not in ("local", "ai") or body.human_color not in ("B", "W"):
            raise HTTPException(400, "只支援同機雙人或人對電腦，請選擇棋色")
        if body.board_size not in (9, 13, 19) or body.rules not in ("chinese", "japanese") or not math.isfinite(body.komi):
            raise HTTPException(400, "請選擇有效棋盤、貼目與規則")
        if not body.name.strip():
            raise HTTPException(400, "桌名不可空白")
        if getattr(ai, "failure", ""):
            raise HTTPException(503, "練習 AI 尚未就緒，請稍後再試")
        async with manager.registry:
            manager.purge()
            if any(room["owner"] == token for room in manager.rooms.values()):
                raise HTTPException(409, "這個瀏覽器已有一桌練習，請繼續原桌，或先收桌再開新局")
            if len(manager.rooms) >= MAX_TABLES:
                raise HTTPException(429, "目前練習暫時額滿，請稍後再試")
            board = "." * body.board_size ** 2
            room = {"id": uuid.uuid4().hex, "name": body.name.strip(), "owner": token, "host": manager.peer(request),
                    "mode": body.mode, "human_color": body.human_color,
                    "settings": {**body.model_dump(include={"board_size", "komi", "rules", "forbid_best"}),
                                 "visits": 128, "allow_pass": True},
                    "board": board, "history": [board], "capture_history": [{"B": 0, "W": 0}],
                    "moves": [], "turn": "B", "version": 0, "status": "analyzing", "candidates": [],
                    "forbidden": None, "error": "", "result": None, "last_activity": clock(), "lock": asyncio.Lock()}
            manager.rooms[room["id"]] = room
            manager.schedule(room)
            return manager.public(room)

    @app.get(base + "/{ident}")
    async def state(ident: str, request: Request):
        room = manager.room(ident, request)
        async with room["lock"]:
            manager.live(room)
            return manager.public(room)

    @app.post(base + "/{ident}/move")
    async def move(ident: str, request: Request, body: LocalMove):
        manager.guard(request)
        room = manager.room(ident, request)
        async with room["lock"]:
            manager.version(room, body)
            if room["status"] != "playing":
                raise HTTPException(409, "目前不能落子，請等待分析完成")
            if room["mode"] == "ai" and room["turn"] != room["human_color"]:
                raise HTTPException(403, "請等待電腦落子")
            try:
                pos = decode(body.move, room["settings"]["board_size"])
                chosen = "pass" if pos is None else encode(pos, room["settings"]["board_size"])
                if chosen == room["forbidden"]:
                    raise IllegalMove("此桌禁止選擇 AI 首選")
                manager.apply_move(room, chosen)
            except IllegalMove as exc:
                raise HTTPException(400, str(exc)) from exc
            if room["status"] != "finished":
                manager.schedule(room)
            return manager.public(room)

    @app.post(base + "/{ident}/undo")
    async def undo(ident: str, request: Request, body: LocalVersion):
        manager.guard(request)
        room = manager.room(ident, request)
        async with room["lock"]:
            manager.version(room, body)
            if not room["moves"]:
                raise HTTPException(409, "沒有可悔棋的棋著")
            count = 1
            if room["mode"] == "ai":
                index = next((i for i in range(len(room["moves"]) - 1, -1, -1)
                              if room["moves"][i][0] == room["human_color"]), None)
                if index is None:
                    raise HTTPException(409, "你尚未落子，沒有可悔棋的回合")
                count = len(room["moves"]) - index
            del room["moves"][-count:]
            del room["history"][-count:]
            del room["capture_history"][-count:]
            room.update(board=room["history"][-1], turn="B" if len(room["moves"]) % 2 == 0 else "W", result=None)
            manager.touch(room)
            manager.schedule(room)
            return manager.public(room)

    @app.post(base + "/{ident}/retry")
    async def retry(ident: str, request: Request, body: LocalVersion):
        manager.guard(request)
        room = manager.room(ident, request)
        async with room["lock"]:
            manager.version(room, body)
            if room["status"] != "error":
                raise HTTPException(409, "目前無需重新分析")
            manager.touch(room)
            manager.schedule(room)
            return manager.public(room)

    @app.post(base + "/{ident}/finish")
    async def finish(ident: str, request: Request, body: LocalVersion):
        manager.guard(request)
        room = manager.room(ident, request)
        async with room["lock"]:
            manager.version(room, body)
            if room["status"] == "finished":
                raise HTTPException(409, "本局已結束")
            winner = ("W" if room["human_color"] == "B" else "B") if room["mode"] == "ai" else None
            manager.ended(room, "resign" if winner else "manual", winner)
            return manager.public(room)

    @app.delete(base + "/{ident}")
    async def close(ident: str, request: Request, body: LocalVersion):
        manager.guard(request)
        room = manager.room(ident, request)
        async with room["lock"]:
            manager.version(room, body)
            async with manager.registry:
                manager.discard(ident)
            return {"closed": True}

    @app.get(base + "/{ident}/sgf")
    async def export(ident: str, request: Request):
        room = manager.room(ident, request)
        async with room["lock"]:
            manager.live(room)
            return Response(sgf(room, room["settings"]), media_type="application/x-go-sgf",
                            headers={"Content-Disposition": f'attachment; filename="practice-{ident}.sgf"',
                                     "Cache-Control": "no-store"})

    return manager
